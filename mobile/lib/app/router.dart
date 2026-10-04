import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../core/auth/session_controller.dart';
import '../core/providers.dart';
import '../features/analysis/analysis_screen.dart';
import '../features/auth/sign_in_screen.dart';
import '../features/chat/chat_screen.dart';
import '../features/chat/conversations_screen.dart';
import '../features/home/home_shell.dart';
import '../features/home/start_screen.dart';
import '../features/home/today_screen.dart';
import '../features/memories/memories_screen.dart';
import '../features/onboarding/onboarding_screen.dart';
import '../features/planning/week_plan_screen.dart';
import '../features/profile/edit_profile_screen.dart';
import '../features/profile/profile_repository.dart';
import '../features/profile/profile_screen.dart';
import '../features/welcome/welcome_screen.dart';

final _rootKey = GlobalKey<NavigatorState>();

/// Where each session state is allowed to be. One rule, so a signed-out user
/// can never reach a signed-in screen by a deep link or the back button.
String? redirectFor(SessionStatus status, String location) {
  final onAuthPage = location == '/sign-in' || location == '/welcome';
  return switch (status) {
    SessionStatus.restoring => location == '/splash' ? null : '/splash',
    SessionStatus.signedOut => onAuthPage ? null : '/welcome',
    SessionStatus.signedIn => (onAuthPage || location == '/splash') ? '/start' : null,
  };
}

final routerProvider = Provider<GoRouter>((ref) {
  // Re-run the redirect whenever the session changes.
  final refresh = ValueNotifier<SessionStatus>(ref.read(sessionProvider));
  ref
    ..listen(sessionProvider, (_, next) => refresh.value = next)
    ..onDispose(refresh.dispose);

  final router = GoRouter(
    navigatorKey: _rootKey,
    initialLocation: '/splash',
    refreshListenable: refresh,
    redirect: (context, state) => redirectFor(refresh.value, state.matchedLocation),
    routes: [
      GoRoute(path: '/splash', builder: (_, _) => const SplashScreen()),
      GoRoute(path: '/welcome', builder: (_, _) => const WelcomeScreen()),
      GoRoute(
        path: '/sign-in',
        builder: (_, state) => SignInScreen(registering: state.uri.queryParameters['mode'] == 'register'),
      ),
      GoRoute(path: '/start', builder: (_, _) => const StartScreen()),
      GoRoute(path: '/onboarding', builder: (_, _) => const OnboardingScreen()),
      GoRoute(path: '/plan', builder: (_, _) => const WeekPlanScreen()),
      StatefulShellRoute.indexedStack(
        builder: (_, _, shell) => HomeShell(shell: shell),
        branches: [
          StatefulShellBranch(
            routes: [GoRoute(path: '/today', builder: (_, _) => const TodayScreen())],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/chats',
                builder: (_, _) => const ConversationsScreen(),
                routes: [
                  // Above the shell, so the conversation has the whole screen.
                  GoRoute(
                    path: 'new',
                    parentNavigatorKey: _rootKey,
                    builder: (_, state) => ChatScreen(draft: state.extra as String?),
                  ),
                  GoRoute(
                    path: ':id',
                    parentNavigatorKey: _rootKey,
                    builder: (_, state) => ChatScreen(conversationId: state.pathParameters['id']),
                  ),
                ],
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [GoRoute(path: '/memories', builder: (_, _) => const MemoriesScreen())],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/profile',
                builder: (_, _) => const ProfileScreen(),
                routes: [
                  GoRoute(
                    path: 'edit',
                    parentNavigatorKey: _rootKey,
                    builder: (_, state) => EditProfileScreen(initial: state.extra as Profile? ?? const Profile()),
                  ),
                  GoRoute(path: 'analysis', parentNavigatorKey: _rootKey, builder: (_, _) => const AnalysisScreen()),
                ],
              ),
            ],
          ),
        ],
      ),
    ],
  );
  ref.onDispose(router.dispose);
  return router;
});

@visibleForTesting
GlobalKey<NavigatorState> get rootNavigatorKey => _rootKey;
