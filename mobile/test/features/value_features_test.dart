import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:personal_intelligence/app/config.dart';
import 'package:personal_intelligence/core/api/sse.dart';
import 'package:personal_intelligence/core/auth/session_controller.dart';
import 'package:personal_intelligence/core/auth/token_store.dart';
import 'package:personal_intelligence/core/providers.dart';
import 'package:personal_intelligence/features/chat/answer_receipt_view.dart';
import 'package:personal_intelligence/features/chat/chat_repository.dart';
import 'package:personal_intelligence/features/chat/chat_screen.dart';
import 'package:personal_intelligence/features/chat/conversations_screen.dart';
import 'package:personal_intelligence/features/profile/delete_account_dialog.dart';
import 'package:personal_intelligence/features/welcome/pillars.dart';
import 'package:personal_intelligence/features/welcome/welcome_screen.dart';

TurnProgress progress(String event, Map<String, Object?> data) =>
    describeEvent(SseEvent(event, jsonEncode(data)))! as TurnProgress;

AnswerReceipt receiptFrom(List<TurnProgress> events) =>
    events.fold(const AnswerReceipt(), (receipt, event) => receipt.apply(event));

void main() {
  group('how a reply was produced', () {
    test('is assembled from the turn\'s counts', () {
      final receipt = receiptFrom([
        progress('query_understood', {
          'needs': ['semantic', 'totals'],
        }),
        progress('retrieval_completed', {'memories': 5, 'timeline_entries': 0, 'has_totals': true}),
        progress('evidence_graded', {'memories_kept': 3, 'sufficient': true}),
        progress('draft_completed', {'attempt': 1, 'failed': false}),
        progress('verification_completed', {'grounded': true}),
      ]);

      expect(receipt.memories, 3);
      expect(receipt.usedTotals, isTrue);
      expect(receipt.grounded, isTrue);
      expect(receipt.rewritten, isFalse);
      expect(receiptSummary(receipt), ['Checked', '3 memories', 'exact totals']);
    });

    test('a rewrite and a second search are noticed', () {
      final receipt = receiptFrom([
        progress('retrieval_completed', {'memories': 1, 'timeline_entries': 2, 'has_totals': false}),
        progress('evidence_graded', {'memories_kept': 0, 'sufficient': false}),
        progress('retrieval_retried', {'attempt': 2, 'memories': 2}),
        progress('draft_completed', {'attempt': 1}),
        progress('verification_completed', {'grounded': false}),
        progress('draft_completed', {'attempt': 2}),
        progress('verification_completed', {'grounded': true}),
      ]);

      expect(receipt.searchedAgain, isTrue);
      expect(receipt.rewritten, isTrue);
      expect(receipt.grounded, isTrue);
      expect(receiptSummary(receipt), contains('your timeline'));
    });

    test('a reply that drew on nothing personal says so', () {
      expect(receiptSummary(const AnswerReceipt(grounded: true)), ['Checked', 'general knowledge']);
    });

    testWidgets('a streamed reply carries its receipt, which explains itself', (tester) async {
      final turns = StreamController<TurnEvent>();
      addTearDown(turns.close);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [chatRepositoryProvider.overrideWithValue(_ScriptedChat(turns))],
          retry: (_, _) => null,
          child: const MaterialApp(home: ChatScreen()),
        ),
      );
      await tester.enterText(find.byKey(const Key('message-input')), 'How much did I study?');
      await tester.pump();
      await tester.tap(find.byKey(const Key('send')));
      await tester.pump();

      turns
        ..add(progress('retrieval_completed', {'memories': 2, 'timeline_entries': 0, 'has_totals': true}))
        ..add(progress('verification_completed', {'grounded': true}))
        ..add(
          TurnCompleted(
            conversationId: 'c1',
            title: 'Study',
            userMessage: ChatMessage.fromJson(_message('u1', 'user', 'How much did I study?')),
            reply: ChatMessage.fromJson(_message('a1', 'assistant', 'Six hours.')),
            degraded: false,
            grounded: true,
          ),
        );
      await tester.pump();

      expect(find.text('Checked · 2 memories · exact totals'), findsOneWidget);
      await tester.tap(find.byKey(const Key('answer-receipt')));
      await tester.pumpAndSettle();
      expect(find.text('How I answered'), findsOneWidget);
      expect(find.text('Counted, not estimated'), findsOneWidget);
    });
  });

  group('deleting', () {
    testWidgets('a swiped conversation is removed after confirming', (tester) async {
      final chats = _Conversations();
      await tester.pumpWidget(
        ProviderScope(
          overrides: [chatRepositoryProvider.overrideWithValue(chats)],
          retry: (_, _) => null,
          child: const MaterialApp(home: ConversationsScreen()),
        ),
      );
      await tester.pumpAndSettle();

      await tester.drag(find.text('Marathon plan'), const Offset(-600, 0));
      await tester.pumpAndSettle();
      expect(find.text('Delete this conversation?'), findsOneWidget);
      await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
      await tester.pumpAndSettle();

      expect(chats.deleted, ['c1']);
      expect(find.text('Marathon plan'), findsNothing);
    });

    group('the account', () {
      late List<http.Request> requests;
      late MemoryTokenStore tokens;

      Future<ProviderContainer> open(WidgetTester tester, {required int status}) async {
        requests = [];
        tokens = MemoryTokenStore(
          TokenPair(accessToken: 'a', refreshToken: 'r', accessExpiresAt: DateTime.now().add(const Duration(hours: 1))),
        );
        final container = ProviderContainer(
          overrides: [
            appConfigProvider.overrideWithValue(AppConfig(apiBaseUrl: Uri.parse('https://api.test'))),
            tokenStoreProvider.overrideWithValue(tokens),
            httpClientProvider.overrideWithValue(
              MockClient((request) async {
                requests.add(request);
                return status == 204
                    ? http.Response('', 204)
                    : http.Response(
                        jsonEncode({
                          'error': {'code': 'INCORRECT_PASSWORD', 'message': 'The password is incorrect.'},
                          'meta': {'request_id': 'r1'},
                        }),
                        status,
                      );
              }),
            ),
          ],
          retry: (_, _) => null,
        );
        addTearDown(container.dispose);
        await tester.pumpWidget(
          UncontrolledProviderScope(
            container: container,
            child: MaterialApp(
              home: Builder(
                builder: (context) =>
                    TextButton(onPressed: () => showDeleteAccountDialog(context), child: const Text('go')),
              ),
            ),
          ),
        );
        await tester.pump();
        await tester.tap(find.text('go'));
        await tester.pumpAndSettle();
        await tester.enterText(find.byKey(const Key('delete-account-password')), 'my-password-123');
        await tester.tap(find.byKey(const Key('delete-account-confirm')));
        await tester.pumpAndSettle();
        return container;
      }

      testWidgets('is deleted with the password, and the session forgotten', (tester) async {
        final container = await open(tester, status: 204);

        expect(requests.single.method, 'DELETE');
        expect(requests.single.url.path, '/api/v1/auth/account');
        expect(jsonDecode(requests.single.body), {'password': 'my-password-123'});
        expect(tokens.tokens, isNull);
        expect(container.read(sessionProvider), SessionStatus.signedOut);
      });

      testWidgets('a wrong password is explained and nothing is forgotten', (tester) async {
        await open(tester, status: 403);

        expect(find.text('That password is not right.'), findsOneWidget);
        expect(tokens.tokens, isNotNull);
      });
    });
  });

  group('the welcome screen', () {
    testWidgets('walks through every reason before asking for an account', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: WelcomeScreen()));

      expect(find.text('See how it works'), findsOneWidget);
      for (final pillar in pillars) {
        await tester.tap(find.byKey(const Key('welcome-next')));
        await tester.pumpAndSettle();
        expect(find.text(pillar.title), findsOneWidget);
      }
      expect(find.text('Create your account'), findsOneWidget);
    });
  });
}

Map<String, dynamic> _message(String id, String role, String content) => {
  'id': id,
  'role': role,
  'content': content,
  'created_at': '2026-10-02T12:00:00Z',
};

class _ScriptedChat implements ChatRepository {
  _ScriptedChat(this._turns);

  final StreamController<TurnEvent> _turns;

  @override
  Stream<TurnEvent> send(String message, {String? conversationId}) => _turns.stream;

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _Conversations implements ChatRepository {
  final deleted = <String>[];

  @override
  Future<PageOf<ConversationSummary>> conversations({int page = 1}) async => PageOf(
    items: [
      if (!deleted.contains('c1'))
        ConversationSummary(id: 'c1', title: 'Marathon plan', messageCount: 4, lastMessageAt: DateTime.now()),
    ],
    page: 1,
    totalPages: 1,
    hasNext: false,
  );

  @override
  Future<void> deleteConversation(String id) async => deleted.add(id);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
