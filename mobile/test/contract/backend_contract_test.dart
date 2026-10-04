/// Runs the app's client code against a real backend.
///
///   CONTRACT_API_BASE_URL=http://localhost:8000 flutter test test/contract
///
/// Skipped without it. The unit tests check the client against the API as the
/// app understands it; this checks that understanding against the server.
library;

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:personal_intelligence/core/api/api_client.dart';
import 'package:personal_intelligence/core/api/api_exception.dart';
import 'package:personal_intelligence/core/auth/token_store.dart';
import 'package:personal_intelligence/features/chat/chat_repository.dart';
import 'package:personal_intelligence/features/memories/memories_repository.dart';
import 'package:personal_intelligence/features/onboarding/onboarding_repository.dart';
import 'package:personal_intelligence/features/profile/profile_repository.dart';

void main() {
  final baseUrl = Platform.environment['CONTRACT_API_BASE_URL'];
  final skip = baseUrl == null ? 'set CONTRACT_API_BASE_URL to run against a backend' : null;

  late ApiClient api;
  late MemoryTokenStore store;
  final email = 'mobile-contract-${DateTime.now().microsecondsSinceEpoch}@example.com';
  const password = 'a-sufficiently-long-password';

  setUpAll(() {
    if (baseUrl == null) {
      return;
    }
    store = MemoryTokenStore();
    api = ApiClient(baseUrl: Uri.parse(baseUrl), httpClient: http.Client(), tokens: store);
  });

  test('registering and signing in stores a session', () async {
    await api.post('/api/v1/auth/register', body: {'email': email, 'password': password}, authenticated: false);
    await api.signIn(email, password);

    expect(store.tokens, isNotNull);
    expect(store.tokens!.accessExpiresAt.isAfter(DateTime.now()), isTrue);
  }, skip: skip);

  test('a wrong password is reported with its code', () async {
    final probe = ApiClient(baseUrl: Uri.parse(baseUrl!), httpClient: http.Client(), tokens: MemoryTokenStore());

    expect(
      probe.signIn(email, 'not-the-right-password'),
      throwsA(isA<ApiException>().having((e) => e.code, 'code', 'INVALID_CREDENTIALS')),
    );
  }, skip: skip);

  test('onboarding starts with a question', () async {
    final onboarding = OnboardingRepository(api);

    expect(await onboarding.state(), OnboardingState.notStarted);
    final turn = await onboarding.start();

    expect(turn.isComplete, isFalse);
    expect(turn.question, isNotEmpty);
    expect(await onboarding.state(), OnboardingState.inProgress);
  }, skip: skip);

  test('a profile can be saved and read back', () async {
    final profiles = ProfileRepository(api);

    final saved = await profiles.save(
      const Profile(
        timezone: 'Europe/London',
        goals: [Goal('Run a half marathon', GoalPriority.high)],
        constraints: ['Only evenings are free'],
        preferences: {'tone': 'direct'},
      ),
    );
    final loaded = await profiles.load();

    expect(saved.goals.single.name, 'Run a half marathon');
    expect(loaded.timezone, 'Europe/London');
    expect(loaded.preferences, {'tone': 'direct'});
  }, skip: skip);

  test('a chat turn streams to completion and is stored', () async {
    final chat = ChatRepository(api);

    final events = await chat.send('How should I train this week?').toList();
    final done = events.whereType<TurnCompleted>().single;

    expect(events.whereType<TurnProgress>(), isNotEmpty);
    expect(done.reply.content, isNotEmpty);
    expect(done.userMessage.content, 'How should I train this week?');

    final conversations = await chat.conversations();
    expect(conversations.items.map((c) => c.id), contains(done.conversationId));

    final page = await chat.conversation(done.conversationId, page: 1);
    expect(page.messages.map((m) => m.id), [done.userMessage.id, done.reply.id]);
  }, skip: skip);

  test('a follow-up continues the same conversation', () async {
    final chat = ChatRepository(api);
    final first = (await chat.send('First question').toList()).whereType<TurnCompleted>().single;

    final second = (await chat.send('Follow-up', conversationId: first.conversationId).toList())
        .whereType<TurnCompleted>()
        .single;

    expect(second.conversationId, first.conversationId);
  }, skip: skip);

  test("someone else's conversation fails the turn as not found", () async {
    final events = await ChatRepository(api)
        .send('hi', conversationId: '00000000-0000-0000-0000-000000000000')
        .toList();

    expect((events.single as TurnFailed).error.code, 'CONVERSATION_NOT_FOUND');
  }, skip: skip);

  test('memories can be listed', () async {
    final page = await MemoriesRepository(api).list(includeArchived: true);

    expect(page.page, 1);
  }, skip: skip);

  test('signing out revokes the session on the server', () async {
    final refreshToken = store.tokens!.refreshToken;
    await api.signOut();

    expect(store.tokens, isNull);
    final probe = ApiClient(baseUrl: Uri.parse(baseUrl!), httpClient: http.Client(), tokens: MemoryTokenStore());
    expect(
      probe.post('/api/v1/auth/refresh', body: {'refresh_token': refreshToken}, authenticated: false),
      throwsA(isA<ApiException>().having((e) => e.code, 'code', 'INVALID_REFRESH_TOKEN')),
    );
  }, skip: skip);
}
