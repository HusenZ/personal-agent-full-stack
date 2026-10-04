import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/ui/components.dart';
import '../../core/ui/feedback.dart';
import '../onboarding/onboarding_repository.dart';

/// Decides where a signed-in user lands: the interview if it has not been
/// finished, the chats otherwise.
class StartScreen extends ConsumerStatefulWidget {
  const StartScreen({super.key});

  @override
  ConsumerState<StartScreen> createState() => _StartScreenState();
}

class _StartScreenState extends ConsumerState<StartScreen> {
  Object? _error;

  @override
  void initState() {
    super.initState();
    _decide();
  }

  Future<void> _decide() async {
    setState(() => _error = null);
    try {
      final state = await ref.read(onboardingRepositoryProvider).state();
      if (mounted) {
        context.go(state == OnboardingState.complete ? '/today' : '/onboarding');
      }
    } on Object catch (error) {
      if (mounted) {
        setState(() => _error = error);
      }
    }
  }

  @override
  Widget build(BuildContext context) => _error == null
      ? const SplashScreen()
      : Scaffold(
          body: ErrorView(error: _error!, onRetry: _decide),
        );
}

/// Shown while the app works out where to go: restoring the session, then
/// asking whether onboarding is finished.
class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const BrandMark(size: 88),
            const SizedBox(height: 24),
            Text('Personal Intelligence', style: theme.textTheme.titleLarge),
            const SizedBox(height: 32),
            SizedBox(
              width: 120,
              child: LinearProgressIndicator(borderRadius: BorderRadius.circular(4), semanticsLabel: 'Loading'),
            ),
          ],
        ),
      ),
    );
  }
}
