import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:personal_intelligence/features/analysis/analysis_screen.dart';
import 'package:personal_intelligence/features/chat/chat_repository.dart';
import 'package:personal_intelligence/features/home/today_screen.dart';
import 'package:personal_intelligence/features/memories/memories_repository.dart';
import 'package:personal_intelligence/features/onboarding/onboarding_repository.dart';
import 'package:personal_intelligence/features/profile/profile_repository.dart';

void main() {
  Future<List<Object?>> open(WidgetTester tester, {Analysis? analysis, Profile profile = const Profile()}) async {
    tester.view.physicalSize = const Size(1080, 4000);
    tester.view.devicePixelRatio = 2.75;
    addTearDown(tester.view.reset);
    final drafts = <Object?>[];
    final router = GoRouter(
      routes: [
        GoRoute(path: '/', builder: (_, _) => const TodayScreen()),
        GoRoute(
          path: '/chats/new',
          builder: (_, state) {
            drafts.add(state.extra);
            return const Scaffold(body: Text('new chat'));
          },
        ),
      ],
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          profileProvider.overrideWith((_) async => profile),
          analysisProvider.overrideWith((_) async => analysis),
          onboardingStatusProvider.overrideWith(
            (_) async => const OnboardingTurn(isComplete: false, questionsAsked: 3, overallConfidence: 0.4),
          ),
          recentMemoriesProvider.overrideWith(
            (_) async => PageOf(
              items: [
                Memory(
                  id: 'm1',
                  content: 'Plays the cello on Sundays',
                  type: 'fact',
                  status: MemoryStatus.active,
                  isSensitive: false,
                  createdAt: DateTime.now(),
                ),
              ],
              page: 1,
              totalPages: 1,
              hasNext: false,
              totalItems: 12,
            ),
          ),
          chatRepositoryProvider.overrideWithValue(_NoChats()),
        ],
        retry: (_, _) => null,
        child: MaterialApp.router(routerConfig: router),
      ),
    );
    await tester.pumpAndSettle();
    return drafts;
  }

  testWidgets('shows what is known, and invites finishing the interview', (tester) async {
    await open(tester);

    expect(find.text('40%'), findsOneWidget);
    expect(find.text('12'), findsOneWidget, reason: 'the total memory count, not just the page');
    expect(find.byKey(const Key('today-onboarding')), findsOneWidget);
    expect(find.text('Plays the cello on Sundays'), findsOneWidget);
    expect(find.text('Get your first analysis'), findsOneWidget);
  });

  testWidgets('a suggestion becomes an editable draft, not a sent message', (tester) async {
    final drafts = await open(
      tester,
      analysis: Analysis(
        createdAt: DateTime.now(),
        degraded: false,
        sections: {
          'suggested_actions': [const Claim('recommendation', 'Block two evenings for study', 0.7)],
        },
      ),
      profile: const Profile(goals: [Goal('Run a half marathon', GoalPriority.high)]),
    );

    await tester.tap(find.text('Block two evenings for study'));
    await tester.pumpAndSettle();
    expect(drafts.single, draftAbout('Block two evenings for study'));
  });

  testWidgets('a goal opens a check-in about it', (tester) async {
    final drafts = await open(tester, profile: const Profile(goals: [Goal('Run a half marathon', GoalPriority.high)]));

    await tester.tap(find.text('Run a half marathon'));
    await tester.pumpAndSettle();
    expect(drafts.single, draftForGoal('Run a half marathon'));
  });
}

class _NoChats implements ChatRepository {
  @override
  Future<PageOf<ConversationSummary>> conversations({int page = 1}) async =>
      const PageOf(items: [], page: 1, totalPages: 0, hasNext: false);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
