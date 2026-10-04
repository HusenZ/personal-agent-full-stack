import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:personal_intelligence/core/api/api_client.dart';
import 'package:personal_intelligence/core/api/api_exception.dart';
import 'package:personal_intelligence/core/auth/token_store.dart';
import 'package:personal_intelligence/features/chat/chat_repository.dart';
import 'package:personal_intelligence/features/chat/chat_screen.dart';

String sse(List<(String, Object)> events) => events.map((e) => 'event: ${e.$1}\ndata: ${jsonEncode(e.$2)}\n\n').join();

Map<String, dynamic> message(String id, String role, String content) => {
  'id': id,
  'role': role,
  'content': content,
  'created_at': '2026-10-02T12:00:00Z',
};

Map<String, dynamic> completed({bool? grounded = true, bool degraded = false}) => {
  'data': {
    'conversation_id': 'c1',
    'title': 'How should I train?',
    'user_message': message('u1', 'user', 'How should I train?'),
    'reply': message('a1', 'assistant', 'Start with three easy runs.'),
    'degraded': degraded,
    'grounded': grounded,
    'run_id': 'r1',
  },
  'meta': {'request_id': 'req'},
};

ChatRepository repositoryStreaming(String body, {int status = 200}) {
  final client = MockClient.streaming((request, _) async {
    expect(request.url.path, '/api/v1/chat/stream');
    return http.StreamedResponse(
      Stream.value(utf8.encode(body)),
      status,
      headers: {'content-type': status == 200 ? 'text/event-stream' : 'application/json'},
    );
  });
  final store = MemoryTokenStore(
    TokenPair(accessToken: 'a', refreshToken: 'r', accessExpiresAt: DateTime.now().add(const Duration(hours: 1))),
  );
  return ChatRepository(ApiClient(baseUrl: Uri.parse('https://api.test'), httpClient: client, tokens: store));
}

void main() {
  group('the event stream', () {
    test('a turn reports its steps and ends completed', () async {
      final events = await repositoryStreaming(
        sse([
          ('query_started', {'conversation_id': 'c1', 'run_id': 'r1'}),
          (
            'query_understood',
            {
              'needs': ['semantic'],
            },
          ),
          ('retrieval_completed', {'memories': 2, 'timeline_entries': 0, 'has_totals': false}),
          ('evidence_graded', {'memories_kept': 1, 'sufficient': true}),
          ('draft_completed', {'attempt': 1, 'failed': false}),
          ('verification_completed', {'grounded': true}),
          ('reply', {'content': 'Start with three easy runs.', 'grounded': true}),
          ('completed', completed()),
        ]),
      ).send('How should I train?').toList();

      expect(
        [
          for (final e in events)
            if (e is TurnProgress) e.stage,
        ],
        [
          TurnStage.understanding,
          TurnStage.recalling,
          TurnStage.weighing,
          TurnStage.drafting,
          TurnStage.verifying,
          TurnStage.verifying,
        ],
      );
      final done = events.last as TurnCompleted;
      expect(done.conversationId, 'c1');
      expect(done.reply.content, 'Start with three easy runs.');
    });

    test('a rejected draft is reported as being rewritten, never shown', () async {
      final events = await repositoryStreaming(
        sse([
          ('draft_completed', {'attempt': 1, 'failed': false}),
          ('verification_completed', {'grounded': false}),
          ('completed', completed()),
        ]),
      ).send('q').toList();

      expect((events[1] as TurnProgress).stage, TurnStage.redrafting);
    });

    test('events this version does not know are ignored', () async {
      final events = await repositoryStreaming(
        sse([
          ('some_future_event', {'x': 1}),
          ('completed', completed()),
        ]),
      ).send('q').toList();

      expect(events.single, isA<TurnCompleted>());
    });

    test('an error event ends the turn as failed, with its request id', () async {
      final events = await repositoryStreaming(
        sse([
          ('query_started', {'conversation_id': 'c1'}),
          ('error', {'code': 'TURN_FAILED', 'message': 'nope', 'request_id': 'req-7'}),
        ]),
      ).send('q').toList();

      final failed = events.last as TurnFailed;
      expect(failed.error.code, 'TURN_FAILED');
      expect(failed.error.requestId, 'req-7');
    });

    test('a stream that ends without a reply is a failure, not a hang', () async {
      final events = await repositoryStreaming(
        sse([
          ('query_started', {'conversation_id': 'c1'}),
        ]),
      ).send('q').toList();

      expect(events.last, isA<TurnFailed>());
    });

    test('an error before the stream opens is a failed turn', () async {
      final events = await repositoryStreaming(
        jsonEncode({
          'error': {'code': 'CONVERSATION_NOT_FOUND', 'message': 'x'},
        }),
        status: 404,
      ).send('q', conversationId: 'gone').toList();

      expect((events.single as TurnFailed).error.code, 'CONVERSATION_NOT_FOUND');
    });

    test('a payload of an unexpected shape fails the turn instead of throwing', () async {
      final events = await repositoryStreaming(
        sse([
          (
            'completed',
            {
              'data': {'conversation_id': 5},
            },
          ),
        ]),
      ).send('q').toList();

      expect(events.single, isA<TurnFailed>());
    });
  });

  group('the chat screen', () {
    Future<_Turns> open(WidgetTester tester, {List<String>? sent}) async {
      final turns = StreamController<TurnEvent>();
      addTearDown(turns.close);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [chatRepositoryProvider.overrideWithValue(_ScriptedChat(turns, sent ?? []))],
          retry: (_, _) => null,
          child: const MaterialApp(home: ChatScreen()),
        ),
      );
      return _Turns(turns);
    }

    Future<void> sendMessage(WidgetTester tester, String text) async {
      await tester.enterText(find.byKey(const Key('message-input')), text);
      // The send button enables on the frame after text arrives.
      await tester.pump();
      await tester.tap(find.byKey(const Key('send')));
      await tester.pump();
    }

    testWidgets('progress is shown while the turn runs, then the stored reply', (tester) async {
      final turns = await open(tester);
      await sendMessage(tester, 'How should I train?');

      expect(find.text('How should I train?'), findsOneWidget);
      expect(find.text('Sending…'), findsOneWidget);

      turns.add(const TurnProgress(TurnStage.verifying));
      await tester.pump();
      expect(find.text(TurnStage.verifying.label), findsOneWidget);

      turns.add(
        TurnCompleted(
          conversationId: 'c1',
          title: 'How should I train?',
          userMessage: ChatMessage.fromJson(message('u1', 'user', 'How should I train?')),
          reply: ChatMessage.fromJson(message('a1', 'assistant', 'Start with three easy runs.')),
          degraded: false,
          grounded: true,
        ),
      );
      await tester.pump();

      expect(find.text('Start with three easy runs.'), findsOneWidget);
      expect(find.byKey(const Key('turn-stage')), findsNothing);
      expect(find.text('Sending…'), findsNothing);
    });

    testWidgets('a reply that failed verification says so', (tester) async {
      final turns = await open(tester);
      await sendMessage(tester, 'q');

      turns.add(
        TurnCompleted(
          conversationId: 'c1',
          title: 't',
          userMessage: ChatMessage.fromJson(message('u1', 'user', 'q')),
          reply: ChatMessage.fromJson(message('a1', 'assistant', 'Since you ran a marathon…')),
          degraded: false,
          grounded: false,
        ),
      );
      await tester.pump();

      expect(find.textContaining("couldn't be checked"), findsOneWidget);
    });

    testWidgets('a failed turn can be retried with the same message', (tester) async {
      final sent = <String>[];
      final turns = await open(tester, sent: sent);
      await sendMessage(tester, 'Plan my week');

      turns.add(const TurnFailed(ApiException.network('offline')));
      await tester.pump();

      expect(find.text('Not sent'), findsOneWidget);
      expect(find.byKey(const Key('turn-failure')), findsOneWidget);

      await tester.tap(find.text('Retry'));
      await tester.pump();

      expect(sent, ['Plan my week', 'Plan my week']);
      expect(find.text('Not sent'), findsNothing);
    });

    testWidgets('nothing can be sent while a turn is running', (tester) async {
      final sent = <String>[];
      await open(tester, sent: sent);
      await sendMessage(tester, 'first');
      await sendMessage(tester, 'second');

      expect(sent, ['first']);
    });
  });
}

/// Replays whatever the test adds, and records what was sent.
class _ScriptedChat implements ChatRepository {
  _ScriptedChat(this._turns, this.sent);

  final StreamController<TurnEvent> _turns;
  final List<String> sent;

  @override
  Stream<TurnEvent> send(String message, {String? conversationId}) {
    sent.add(message);
    return _turns.stream.isBroadcast ? _turns.stream : (_broadcast ??= _turns.stream.asBroadcastStream());
  }

  Stream<TurnEvent>? _broadcast;

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

/// What a test may do with a scripted turn: add events. The stream itself is
/// owned, and closed, by the helper that opened the screen.
class _Turns {
  _Turns(this._controller);

  final StreamController<TurnEvent> _controller;

  void add(TurnEvent event) => _controller.add(event);
}
