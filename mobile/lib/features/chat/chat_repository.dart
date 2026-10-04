import 'dart:async';
import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_client.dart';
import '../../core/api/api_exception.dart';
import '../../core/api/sse.dart';
import '../../core/providers.dart';

enum MessageRole { user, assistant }

class ChatMessage {
  const ChatMessage({
    required this.role,
    required this.content,
    this.id,
    this.createdAt,
    this.pending = false,
    this.failed = false,
    this.grounded,
    this.degraded = false,
    this.receipt,
  });

  factory ChatMessage.fromJson(Map<String, dynamic> json) => ChatMessage(
    id: json['id'] as String,
    role: json['role'] == 'assistant' ? MessageRole.assistant : MessageRole.user,
    content: json['content'] as String,
    createdAt: DateTime.parse(json['created_at'] as String),
  );

  final String? id;
  final MessageRole role;
  final String content;
  final DateTime? createdAt;

  /// Sent, but the turn has not finished storing it yet.
  final bool pending;

  /// The turn failed; nothing was stored and it can be sent again.
  final bool failed;

  /// Only known for replies produced in this session: false means the best
  /// draft still had statements verification could not support.
  final bool? grounded;

  /// The assistant could not be reached and this is the fallback text.
  final bool degraded;

  /// How the reply was produced. Only known for replies streamed in this
  /// session; the API does not keep it with stored messages.
  final AnswerReceipt? receipt;

  ChatMessage copyWith({String? id, DateTime? createdAt, bool? pending, bool? failed}) => ChatMessage(
    id: id ?? this.id,
    role: role,
    content: content,
    createdAt: createdAt ?? this.createdAt,
    pending: pending ?? this.pending,
    failed: failed ?? this.failed,
    grounded: grounded,
    degraded: degraded,
    receipt: receipt,
  );
}

/// What went into one reply, gathered from the turn's progress events: counts
/// and outcomes only, never the content the server keeps to itself.
class AnswerReceipt {
  const AnswerReceipt({
    this.plannedNeeds = const [],
    this.memoriesFound = 0,
    this.memoriesUsed,
    this.timelineEntries = 0,
    this.usedTotals = false,
    this.searchedAgain = false,
    this.drafts = 0,
    this.grounded,
  });

  /// What the question was judged to need: "semantic", "totals", "timeline"…
  final List<String> plannedNeeds;
  final int memoriesFound;

  /// After irrelevant memories were set aside; null if they were not graded.
  final int? memoriesUsed;
  final int timelineEntries;

  /// Exact figures computed from records rather than estimated by the model.
  final bool usedTotals;
  final bool searchedAgain;
  final int drafts;
  final bool? grounded;

  int get memories => memoriesUsed ?? memoriesFound;
  bool get rewritten => drafts > 1;

  AnswerReceipt apply(TurnProgress progress) {
    final data = progress.data;
    return switch (progress.event) {
      'query_understood' => _copy(plannedNeeds: [for (final need in data['needs'] as List<dynamic>? ?? []) '$need']),
      'retrieval_completed' => _copy(
        memoriesFound: data['memories'] as int? ?? 0,
        timelineEntries: data['timeline_entries'] as int? ?? 0,
        usedTotals: data['has_totals'] as bool? ?? false,
      ),
      'evidence_graded' => _copy(memoriesUsed: data['memories_kept'] as int?),
      'retrieval_retried' => _copy(searchedAgain: true, memoriesFound: data['memories'] as int? ?? memoriesFound),
      'draft_completed' => _copy(drafts: data['attempt'] as int? ?? drafts + 1),
      'verification_completed' => _copy(grounded: data['grounded'] as bool?),
      _ => this,
    };
  }

  AnswerReceipt _copy({
    List<String>? plannedNeeds,
    int? memoriesFound,
    int? memoriesUsed,
    int? timelineEntries,
    bool? usedTotals,
    bool? searchedAgain,
    int? drafts,
    bool? grounded,
  }) => AnswerReceipt(
    plannedNeeds: plannedNeeds ?? this.plannedNeeds,
    memoriesFound: memoriesFound ?? this.memoriesFound,
    memoriesUsed: memoriesUsed ?? this.memoriesUsed,
    timelineEntries: timelineEntries ?? this.timelineEntries,
    usedTotals: usedTotals ?? this.usedTotals,
    searchedAgain: searchedAgain ?? this.searchedAgain,
    drafts: drafts ?? this.drafts,
    grounded: grounded ?? this.grounded,
  );
}

class ConversationSummary {
  const ConversationSummary({
    required this.id,
    required this.title,
    required this.messageCount,
    required this.lastMessageAt,
  });

  factory ConversationSummary.fromJson(Map<String, dynamic> json) => ConversationSummary(
    id: json['id'] as String,
    title: json['title'] as String,
    messageCount: json['message_count'] as int,
    lastMessageAt: DateTime.parse(json['last_message_at'] as String),
  );

  final String id;
  final String title;
  final int messageCount;
  final DateTime lastMessageAt;
}

class PageOf<T> {
  const PageOf({
    required this.items,
    required this.page,
    required this.totalPages,
    required this.hasNext,
    this.totalItems,
  });

  factory PageOf.fromResponse(ApiResponse response, T Function(Map<String, dynamic>) item) => PageOf(
    items: [for (final entry in response.list) item(entry as Map<String, dynamic>)],
    page: response.meta['page'] as int,
    totalPages: response.meta['total_pages'] as int,
    hasNext: response.meta['has_next'] as bool,
    totalItems: response.meta['total_items'] as int?,
  );

  final List<T> items;
  final int page;
  final int totalPages;
  final bool hasNext;

  /// Across every page, when the server reported it.
  final int? totalItems;

  int get count => totalItems ?? items.length;
}

class ConversationPage {
  const ConversationPage({required this.title, required this.messages, required this.page, required this.totalPages});

  final String title;
  final List<ChatMessage> messages;
  final int page;
  final int totalPages;
}

/// What the assistant is doing next, as reported by the stream.
enum TurnStage {
  understanding('Understanding your question…'),
  recalling('Recalling what you have told me…'),
  weighing('Weighing what I found…'),
  searchingAgain('Looking a little further…'),
  drafting('Writing a reply…'),
  verifying('Checking the reply against what I know…'),
  redrafting('Rewriting to stick to what I know…');

  const TurnStage(this.label);

  final String label;
}

sealed class TurnEvent {
  const TurnEvent();
}

class TurnProgress extends TurnEvent {
  const TurnProgress(this.stage, {this.event = '', this.data = const {}});

  final TurnStage stage;

  /// The server event this came from, and its allow-listed counts.
  final String event;
  final Map<String, dynamic> data;
}

/// The verified reply. Arrives after the exchange has been stored.
class TurnReply extends TurnEvent {
  const TurnReply({required this.content, required this.grounded});

  final String content;
  final bool? grounded;
}

class TurnCompleted extends TurnEvent {
  const TurnCompleted({
    required this.conversationId,
    required this.title,
    required this.userMessage,
    required this.reply,
    required this.degraded,
    required this.grounded,
  });

  final String conversationId;
  final String title;
  final ChatMessage userMessage;
  final ChatMessage reply;
  final bool degraded;
  final bool? grounded;
}

class TurnFailed extends TurnEvent {
  const TurnFailed(this.error);

  final ApiException error;
}

/// Turns one server event into what the screen should show next.
///
/// Each event reports a step that has finished, so the stage shown is the one
/// that follows it. Events this version does not know are ignored, so the
/// server can add progress events without breaking older apps.
TurnEvent? describeEvent(SseEvent event) {
  final data = event.data.isEmpty ? const <String, dynamic>{} : jsonDecode(event.data) as Map<String, dynamic>;
  return switch (event.event) {
    'query_started' => TurnProgress(TurnStage.understanding, event: event.event, data: data),
    'query_understood' => TurnProgress(TurnStage.recalling, event: event.event, data: data),
    'retrieval_completed' => TurnProgress(
      (data['memories'] as int? ?? 0) > 0 ? TurnStage.weighing : TurnStage.drafting,
      event: event.event,
      data: data,
    ),
    'evidence_graded' => TurnProgress(
      data['sufficient'] == false ? TurnStage.searchingAgain : TurnStage.drafting,
      event: event.event,
      data: data,
    ),
    'retrieval_retried' => TurnProgress(TurnStage.weighing, event: event.event, data: data),
    'draft_completed' => TurnProgress(TurnStage.verifying, event: event.event, data: data),
    'verification_completed' => TurnProgress(
      data['grounded'] == false ? TurnStage.redrafting : TurnStage.verifying,
      event: event.event,
      data: data,
    ),
    'reply' => TurnReply(content: data['content'] as String, grounded: data['grounded'] as bool?),
    'completed' => _completed(data['data'] as Map<String, dynamic>),
    'error' => TurnFailed(
      ApiException(
        statusCode: 500,
        code: data['code'] as String? ?? 'TURN_FAILED',
        message: data['message'] as String? ?? 'The reply could not be completed.',
        requestId: data['request_id'] as String?,
      ),
    ),
    _ => null,
  };
}

TurnCompleted _completed(Map<String, dynamic> data) => TurnCompleted(
  conversationId: data['conversation_id'] as String,
  title: data['title'] as String,
  userMessage: ChatMessage.fromJson(data['user_message'] as Map<String, dynamic>),
  reply: ChatMessage.fromJson(data['reply'] as Map<String, dynamic>),
  degraded: data['degraded'] as bool? ?? false,
  grounded: data['grounded'] as bool?,
);

class ChatRepository {
  ChatRepository(this._api, {this.idleTimeout = const Duration(seconds: 90)});

  final ApiClient _api;

  /// Longest silence tolerated between events. A turn emits one per step, and
  /// no single step should take this long; past it, the turn is reported as
  /// failed rather than left spinning.
  final Duration idleTimeout;

  Future<PageOf<ConversationSummary>> conversations({int page = 1}) async => PageOf.fromResponse(
    await _api.get('/api/v1/conversations', query: {'page': '$page', 'page_size': '20'}),
    ConversationSummary.fromJson,
  );

  Future<ConversationPage> conversation(String id, {required int page, int pageSize = 50}) async {
    final response = await _api.get('/api/v1/conversations/$id', query: {'page': '$page', 'page_size': '$pageSize'});
    final detail = response.object;
    return ConversationPage(
      title: detail['title'] as String,
      messages: [
        for (final message in detail['messages'] as List<dynamic>)
          ChatMessage.fromJson(message as Map<String, dynamic>),
      ],
      page: response.meta['page'] as int,
      totalPages: response.meta['total_pages'] as int,
    );
  }

  /// Removes the conversation and its messages. Memories learned from it
  /// stay, and are managed on their own.
  Future<void> deleteConversation(String id) => _api.delete('/api/v1/conversations/$id');

  /// Sends a message and reports the turn as it runs.
  ///
  /// Always ends with [TurnCompleted] or [TurnFailed]. Cancelling the
  /// subscription closes the connection, and the server then stores nothing.
  Stream<TurnEvent> send(String message, {String? conversationId}) async* {
    final StreamedEvents source;
    try {
      final response = await _api.openEventStream(
        '/api/v1/chat/stream',
        body: {'message': message, 'conversation_id': ?conversationId},
      );
      source = parseSse(response.stream);
    } on ApiException catch (error) {
      yield TurnFailed(error);
      return;
    }

    try {
      await for (final raw in source.timeout(idleTimeout)) {
        final event = describeEvent(raw);
        if (event == null) {
          continue;
        }
        yield event;
        if (event is TurnCompleted || event is TurnFailed) {
          return;
        }
      }
      yield const TurnFailed(ApiException.network('The connection closed before the reply arrived.'));
    } on TimeoutException {
      yield const TurnFailed(ApiException.network('The assistant stopped responding.'));
    } on FormatException {
      yield const TurnFailed(
        ApiException(statusCode: 0, code: 'BAD_RESPONSE', message: 'The server sent something unexpected.'),
      );
      // Unexpected payload shapes become a retryable failed turn.
      // ignore: avoid_catching_errors
    } on TypeError {
      yield const TurnFailed(
        ApiException(statusCode: 0, code: 'BAD_RESPONSE', message: 'The server sent something unexpected.'),
      );
    }
  }
}

typedef StreamedEvents = Stream<SseEvent>;

final chatRepositoryProvider = Provider<ChatRepository>((ref) => ChatRepository(ref.watch(apiClientProvider)));
