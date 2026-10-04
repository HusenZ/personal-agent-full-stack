import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:personal_intelligence/app/config.dart';
import 'package:personal_intelligence/app/router.dart';
import 'package:personal_intelligence/core/auth/session_controller.dart';
import 'package:personal_intelligence/core/auth/token_store.dart';
import 'package:personal_intelligence/core/providers.dart';
import 'package:personal_intelligence/features/auth/sign_in_screen.dart';
import 'package:personal_intelligence/features/memories/memories_repository.dart';
import 'package:personal_intelligence/features/memories/memories_screen.dart';

void main() {
  group('where each session state may go', () {
    test('signed out, every page leads to the welcome screen', () {
      for (final location in [
        '/today',
        '/chats',
        '/chats/abc',
        '/memories',
        '/profile/edit',
        '/onboarding',
        '/start',
      ]) {
        expect(redirectFor(SessionStatus.signedOut, location), '/welcome', reason: location);
      }
      expect(redirectFor(SessionStatus.signedOut, '/sign-in'), isNull);
      expect(redirectFor(SessionStatus.signedOut, '/welcome'), isNull);
    });

    test('signed in, the sign-in page leads into the app', () {
      expect(redirectFor(SessionStatus.signedIn, '/sign-in'), '/start');
      expect(redirectFor(SessionStatus.signedIn, '/welcome'), '/start');
      expect(redirectFor(SessionStatus.signedIn, '/splash'), '/start');
      expect(redirectFor(SessionStatus.signedIn, '/chats/abc'), isNull);
    });

    test('while the session is being restored, nothing else is shown', () {
      expect(redirectFor(SessionStatus.restoring, '/chats'), '/splash');
    });
  });

  group('signing in', () {
    late MemoryTokenStore store;
    late List<String> calls;

    Future<ProviderContainer> pump(WidgetTester tester) async {
      store = MemoryTokenStore();
      calls = [];
      final client = MockClient((request) async {
        calls.add(request.url.path);
        return http.Response(
          jsonEncode({
            'data': {
              'access_token': 'a',
              'refresh_token': 'r',
              'access_token_expires_at': DateTime.now().add(const Duration(minutes: 15)).toUtc().toIso8601String(),
              'refresh_token_expires_at': DateTime.now().add(const Duration(days: 30)).toUtc().toIso8601String(),
            },
          }),
          request.url.path.endsWith('register') ? 201 : 200,
        );
      });
      final container = ProviderContainer(
        overrides: [
          appConfigProvider.overrideWithValue(AppConfig(apiBaseUrl: Uri.parse('https://api.test'))),
          tokenStoreProvider.overrideWithValue(store),
          httpClientProvider.overrideWithValue(client),
        ],
        retry: (_, _) => null,
      );
      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: container,
          child: const MaterialApp(home: SignInScreen()),
        ),
      );
      await tester.pump();
      return container;
    }

    testWidgets('invalid details are explained before anything is sent', (tester) async {
      await pump(tester);
      await tester.tap(find.byKey(const Key('mode-register')));
      await tester.pump();

      await tester.enterText(find.byKey(const Key('email')), 'not-an-email');
      await tester.enterText(find.byKey(const Key('password')), 'short');
      await tester.ensureVisible(find.byKey(const Key('submit')));
      await tester.tap(find.byKey(const Key('submit')));
      await tester.pump();

      expect(find.text('Enter a valid email'), findsOneWidget);
      expect(find.text('Use at least $minimumPasswordLength characters'), findsOneWidget);
      expect(calls, isEmpty);
    });

    testWidgets('signing in stores the session securely and signs in', (tester) async {
      final container = await pump(tester);

      await tester.enterText(find.byKey(const Key('email')), 'ada@example.com');
      await tester.enterText(find.byKey(const Key('password')), 'a-sufficiently-long-password');
      await tester.ensureVisible(find.byKey(const Key('submit')));
      await tester.tap(find.byKey(const Key('submit')));
      await tester.pumpAndSettle();

      expect(calls, ['/api/v1/auth/login']);
      expect(store.tokens?.refreshToken, 'r');
      expect(container.read(sessionProvider), SessionStatus.signedIn);
    });
  });

  group('memories', () {
    testWidgets('deleting asks first, and cancelling deletes nothing', (tester) async {
      final repository = _Memories();
      await tester.pumpWidget(
        ProviderScope(
          overrides: [memoriesRepositoryProvider.overrideWithValue(repository)],
          retry: (_, _) => null,
          child: const MaterialApp(home: MemoriesScreen()),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('I play the cello'), findsOneWidget);

      Future<void> chooseDelete() async {
        await tester.tap(find.byTooltip('Options'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Delete').last);
        await tester.pumpAndSettle();
      }

      await chooseDelete();
      expect(find.text('Delete this memory?'), findsOneWidget);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(repository.deleted, isEmpty);

      await chooseDelete();
      await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
      await tester.pumpAndSettle();
      expect(repository.deleted, ['m1']);
    });
  });
}

class _Memories implements MemoriesRepository {
  final deleted = <String>[];

  @override
  Future<void> delete(String id) async => deleted.add(id);

  @override
  Future<PageOfMemories> list({required bool includeArchived, int page = 1}) async => PageOfMemories(
    items: [
      Memory(
        id: 'm1',
        content: 'I play the cello',
        type: 'fact',
        status: MemoryStatus.active,
        isSensitive: false,
        createdAt: DateTime.utc(2026, 9, 1),
      ),
    ],
    page: 1,
    totalPages: 1,
    hasNext: false,
  );

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
