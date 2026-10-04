import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_exception.dart';
import 'chat_repository.dart';
import 'conversations_controller.dart';

class ChatState {
  const ChatState({
    this.conversationId,
    this.title,
    this.messages = const [],
    this.loading = false,
    this.loadError,
    this.earliestPage = 1,
    this.loadingEarlier = false,
    this.stage,
    this.lastFailure,
  });

  final String? conversationId;
  final String? title;
  final List<ChatMessage> messages;
  final bool loading;
  final Object? loadError;

  /// The oldest page of messages loaded; earlier ones exist while it is > 1.
  final int earliestPage;
  final bool loadingEarlier;

  /// Set while a turn is running.
  final TurnStage? stage;
  final ApiException? lastFailure;

  bool get sending => stage != null;
  bool get hasEarlier => earliestPage > 1;

  ChatState copyWith({
    String? conversationId,
    String? title,
    List<ChatMessage>? messages,
    bool? loading,
    Object? loadError,
    bool clearLoadError = false,
    int? earliestPage,
    bool? loadingEarlier,
    TurnStage? stage,
    bool clearStage = false,
    ApiException? lastFailure,
    bool clearFailure = false,
  }) => ChatState(
    conversationId: conversationId ?? this.conversationId,
    title: title ?? this.title,
    messages: messages ?? this.messages,
    loading: loading ?? this.loading,
    loadError: clearLoadError ? null : (loadError ?? this.loadError),
    earliestPage: earliestPage ?? this.earliestPage,
    loadingEarlier: loadingEarlier ?? this.loadingEarlier,
    stage: clearStage ? null : (stage ?? this.stage),
    lastFailure: clearFailure ? null : (lastFailure ?? this.lastFailure),
  );
}

/// One open conversation: its messages, and the turn in progress if any.
///
/// Keyed per screen rather than per conversation id, so two "new chat" screens
/// never share state, and a new conversation keeps its controller when the
/// server assigns its id.
class ChatController extends Notifier<ChatState> {
  ChatController(this.key);

  /// `conversation:<id>` for an existing conversation, anything else for new.
  final String key;

  static const _pageSize = 50;
  StreamSubscription<TurnEvent>? _turn;
  AnswerReceipt _receipt = const AnswerReceipt();

  ChatRepository get _repository => ref.read(chatRepositoryProvider);

  @override
  ChatState build() {
    // Leaving the screen ends the turn; the server stores nothing.
    ref.onDispose(() => _turn?.cancel());
    final id = key.startsWith('conversation:') ? key.substring('conversation:'.length) : null;
    if (id == null) {
      return const ChatState();
    }
    Future.microtask(load);
    return ChatState(conversationId: id, loading: true);
  }

  /// Opens on the newest messages: the API pages oldest first, so the last
  /// page is found from the first page's total.
  Future<void> load() async {
    final id = state.conversationId!;
    state = state.copyWith(loading: true, clearLoadError: true);
    try {
      var page = await _repository.conversation(id, page: 1, pageSize: _pageSize);
      if (page.totalPages > 1) {
        page = await _repository.conversation(id, page: page.totalPages, pageSize: _pageSize);
      }
      if (!ref.mounted) {
        return;
      }
      state = state.copyWith(title: page.title, messages: page.messages, earliestPage: page.page, loading: false);
    } on Object catch (error) {
      if (ref.mounted) {
        state = state.copyWith(loading: false, loadError: error);
      }
    }
  }

  Future<void> loadEarlier() async {
    if (!state.hasEarlier || state.loadingEarlier) {
      return;
    }
    state = state.copyWith(loadingEarlier: true);
    try {
      final page = await _repository.conversation(
        state.conversationId!,
        page: state.earliestPage - 1,
        pageSize: _pageSize,
      );
      if (ref.mounted) {
        state = state.copyWith(
          messages: [...page.messages, ...state.messages],
          earliestPage: page.page,
          loadingEarlier: false,
        );
      }
    } on Object {
      if (ref.mounted) {
        state = state.copyWith(loadingEarlier: false);
      }
    }
  }

  void send(String text) {
    final message = text.trim();
    if (message.isEmpty || state.sending) {
      return;
    }

    final pending = ChatMessage(role: MessageRole.user, content: message, pending: true);
    _receipt = const AnswerReceipt();
    state = state.copyWith(
      messages: [...state.messages.where((m) => !m.failed), pending],
      stage: TurnStage.understanding,
      clearFailure: true,
    );

    _turn = _repository
        .send(message, conversationId: state.conversationId)
        .listen(
          _onEvent,
          onError: (Object error) =>
              _fail(error is ApiException ? error : const ApiException.network('The connection was interrupted.')),
        );
  }

  /// Sends the failed message again. Safe: a failed turn stored nothing.
  void retry() {
    final failed = state.messages.lastWhere(
      (m) => m.failed,
      orElse: () => const ChatMessage(role: MessageRole.user, content: ''),
    );
    if (failed.content.isNotEmpty) {
      send(failed.content);
    }
  }

  void _onEvent(TurnEvent event) {
    if (!ref.mounted) {
      return;
    }
    switch (event) {
      case TurnProgress(:final stage):
        _receipt = _receipt.apply(event);
        state = state.copyWith(stage: stage);
      case TurnReply():
        // Show the reply from the completed event, once it's stored.
        break;
      case TurnCompleted():
        final messages = [...state.messages];
        final index = messages.lastIndexWhere((m) => m.pending);
        if (index != -1) {
          messages[index] = messages[index].copyWith(
            id: event.userMessage.id,
            createdAt: event.userMessage.createdAt,
            pending: false,
          );
        }
        messages.add(
          ChatMessage(
            id: event.reply.id,
            role: MessageRole.assistant,
            content: event.reply.content,
            createdAt: event.reply.createdAt,
            grounded: event.grounded,
            degraded: event.degraded,
            receipt: event.degraded ? null : _receipt,
          ),
        );
        state = state.copyWith(
          conversationId: event.conversationId,
          title: event.title,
          messages: messages,
          clearStage: true,
        );
        ref.invalidate(conversationsProvider);
      case TurnFailed(:final error):
        _fail(error);
    }
  }

  void _fail(ApiException error) {
    if (!ref.mounted) {
      return;
    }
    state = state.copyWith(
      messages: [
        for (final message in state.messages)
          message.pending ? message.copyWith(pending: false, failed: true) : message,
      ],
      clearStage: true,
      lastFailure: error,
    );
  }
}

final chatControllerProvider = NotifierProvider.autoDispose.family<ChatController, ChatState, String>(
  ChatController.new,
);
