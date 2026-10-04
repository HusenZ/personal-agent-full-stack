import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/api/api_exception.dart';
import '../../core/ui/components.dart';
import '../../core/ui/feedback.dart';
import 'onboarding_repository.dart';

/// The adaptive interview: one question at a time, chosen by the backend
/// from what it still does not know.
class OnboardingScreen extends ConsumerStatefulWidget {
  const OnboardingScreen({super.key});

  @override
  ConsumerState<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends ConsumerState<OnboardingScreen> {
  final _answer = TextEditingController();
  OnboardingTurn? _turn;
  Object? _loadError;
  bool _sending = false;

  @override
  void initState() {
    super.initState();
    _start();
  }

  @override
  void dispose() {
    _answer.dispose();
    super.dispose();
  }

  Future<void> _start() async {
    setState(() => _loadError = null);
    try {
      final turn = await ref.read(onboardingRepositoryProvider).start();
      if (mounted) {
        setState(() => _turn = turn);
      }
    } on ApiException catch (error) {
      if (!mounted) {
        return;
      }
      if (error.code == 'ONBOARDING_ALREADY_COMPLETED') {
        context.go('/today');
      } else {
        setState(() => _loadError = error);
      }
    }
  }

  Future<void> _submit() async {
    final text = _answer.text.trim();
    if (text.isEmpty || _sending) {
      return;
    }
    setState(() => _sending = true);
    try {
      final turn = await ref.read(onboardingRepositoryProvider).answer(text);
      if (!mounted) {
        return;
      }
      _answer.clear();
      setState(() => _turn = turn);
    } on Object catch (error) {
      if (mounted) {
        showError(context, error);
      }
    } finally {
      if (mounted) {
        setState(() => _sending = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final turn = _turn;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Getting to know you'),
        actions: [
          if (turn != null && !turn.isComplete)
            TextButton(onPressed: () => context.go('/today'), child: const Text('Finish later')),
          const SizedBox(width: 8),
        ],
      ),
      body: SafeArea(
        child: switch ((turn, _loadError)) {
          (_, final Object error) => ErrorView(error: error, onRetry: _start),
          (null, _) => const Center(child: CircularProgressIndicator()),
          (final OnboardingTurn turn, _) when turn.isComplete => _Complete(turn: turn),
          (final OnboardingTurn turn, _) => _Question(
            turn: turn,
            answer: _answer,
            sending: _sending,
            onSubmit: _submit,
          ),
        },
      ),
    );
  }
}

class _Question extends StatelessWidget {
  const _Question({required this.turn, required this.answer, required this.sending, required this.onSubmit});

  final OnboardingTurn turn;
  final TextEditingController answer;
  final bool sending;
  final VoidCallback onSubmit;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final confidence = turn.overallConfidence.clamp(0.0, 1.0);

    return Column(
      children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 20),
            children: [
              Row(
                children: [
                  _ConfidenceRing(value: confidence),
                  const SizedBox(width: 16),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Question ${turn.questionsAsked}', style: theme.textTheme.titleMedium),
                        const SizedBox(height: 2),
                        Text(
                          'Each answer fills in more of the picture.',
                          style: theme.textTheme.bodySmall?.copyWith(color: colors.onSurfaceVariant),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 28),
              AnimatedSwitcher(
                duration: const Duration(milliseconds: 350),
                transitionBuilder: (child, animation) => FadeTransition(
                  opacity: animation,
                  child: SlideTransition(
                    position: Tween(begin: const Offset(0, 0.06), end: Offset.zero).animate(animation),
                    child: child,
                  ),
                ),
                child: Row(
                  key: ValueKey(turn.questionsAsked),
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Padding(padding: EdgeInsets.only(top: 4), child: AssistantAvatar(size: 36)),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Container(
                        padding: const EdgeInsets.all(18),
                        decoration: BoxDecoration(
                          color: colors.surfaceContainerHigh,
                          borderRadius: const BorderRadius.only(
                            topRight: Radius.circular(22),
                            bottomLeft: Radius.circular(22),
                            bottomRight: Radius.circular(22),
                            topLeft: Radius.circular(6),
                          ),
                        ),
                        child: Text(
                          turn.question ?? '',
                          key: const Key('question'),
                          style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w600, height: 1.35),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 24),
              TextField(
                key: const Key('answer'),
                controller: answer,
                minLines: 4,
                maxLines: 8,
                maxLength: 4000,
                enabled: !sending,
                textCapitalization: TextCapitalization.sentences,
                decoration: const InputDecoration(
                  hintText: 'Answer in your own words. A sentence or two is plenty.',
                  alignLabelWithHint: true,
                ),
              ),
            ],
          ),
        ),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
          child: ListenableBuilder(
            listenable: answer,
            builder: (context, _) => FilledButton.icon(
              key: const Key('send-answer'),
              onPressed: sending || answer.text.trim().isEmpty ? null : onSubmit,
              iconAlignment: IconAlignment.end,
              icon: sending ? const ButtonProgress() : const Icon(Icons.arrow_forward),
              label: const Text('Continue'),
            ),
          ),
        ),
      ],
    );
  }
}

/// How much of the picture the interview has so far, as a ring.
class _ConfidenceRing extends StatelessWidget {
  const _ConfidenceRing({required this.value});

  final double value;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final percent = (value * 100).round();
    return Semantics(
      label: 'I know about $percent% of what I need',
      child: ExcludeSemantics(
        child: SizedBox.square(
          dimension: 56,
          child: TweenAnimationBuilder<double>(
            tween: Tween(begin: 0, end: value),
            duration: const Duration(milliseconds: 600),
            curve: Curves.easeOutCubic,
            builder: (context, animated, _) => Stack(
              fit: StackFit.expand,
              children: [
                CircularProgressIndicator(
                  value: animated,
                  strokeWidth: 5,
                  strokeCap: StrokeCap.round,
                  backgroundColor: theme.colorScheme.surfaceContainerHighest,
                ),
                Center(
                  child: Text(
                    '${(animated * 100).round()}%',
                    style: theme.textTheme.labelLarge?.copyWith(fontWeight: FontWeight.w700),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _Complete extends StatelessWidget {
  const _Complete({required this.turn});

  final OnboardingTurn turn;

  @override
  Widget build(BuildContext context) => EmptyState(
    icon: Icons.celebration_outlined,
    title: 'Thanks, that is a good start',
    message:
        'Your answers shaped your profile after ${turn.questionsAsked} questions. '
        'Everything keeps improving as we talk.',
    action: FilledButton.icon(
      onPressed: () => context.go('/today'),
      icon: const Icon(Icons.arrow_forward),
      label: const Text('See what I learned'),
    ),
  );
}
