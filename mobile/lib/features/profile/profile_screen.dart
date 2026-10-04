import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/providers.dart';
import '../../core/ui/components.dart';
import '../../core/ui/feedback.dart';
import '../../core/ui/formatting.dart';
import '../onboarding/onboarding_repository.dart';
import '../welcome/pillars.dart';
import 'delete_account_dialog.dart';
import 'profile_repository.dart';

final onboardingStateProvider = FutureProvider.autoDispose<OnboardingState>(
  (ref) => ref.watch(onboardingRepositoryProvider).state(),
);

Color priorityColor(GoalPriority priority, ColorScheme colors) => switch (priority) {
  GoalPriority.high => colors.error,
  GoalPriority.medium => Colors.orange.shade700,
  GoalPriority.low => colors.outline,
};

class ProfileScreen extends ConsumerWidget {
  const ProfileScreen({super.key});

  Future<void> _signOut(BuildContext context, WidgetRef ref) async {
    if (await confirm(context, title: 'Sign out?', message: 'You can sign back in at any time.', action: 'Sign out')) {
      await ref.read(sessionProvider.notifier).signOut();
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = ref.watch(profileProvider);
    final onboarding = ref.watch(onboardingStateProvider).value;
    final theme = Theme.of(context);
    final colors = theme.colorScheme;

    return Scaffold(
      appBar: AppBar(
        title: const Text('You'),
        actions: [
          if (profile case AsyncData(:final value))
            IconButton(
              tooltip: 'Edit profile',
              icon: const Icon(Icons.edit_outlined),
              onPressed: () => context.push('/profile/edit', extra: value),
            ),
          const SizedBox(width: 4),
        ],
      ),
      body: switch (profile) {
        AsyncData(:final value) => RefreshIndicator(
          onRefresh: () => ref.refresh(profileProvider.future),
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
            children: [
              _Header(profile: value),
              if (onboarding != null && onboarding != OnboardingState.complete) ...[
                const SizedBox(height: 12),
                Card(
                  color: colors.primaryContainer,
                  clipBehavior: Clip.antiAlias,
                  child: ListTile(
                    contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                    leading: Icon(Icons.chat_bubble_outline, color: colors.onPrimaryContainer),
                    title: Text(
                      'Finish getting to know you',
                      style: TextStyle(color: colors.onPrimaryContainer, fontWeight: FontWeight.w600),
                    ),
                    subtitle: Text(
                      'A few questions make every answer more useful.',
                      style: TextStyle(color: colors.onPrimaryContainer),
                    ),
                    trailing: Icon(Icons.arrow_forward, color: colors.onPrimaryContainer),
                    onTap: () => context.push('/onboarding'),
                  ),
                ),
              ],
              const SectionHeader('Goals'),
              _Group(
                empty: 'No goals yet. Add one so answers can work towards it.',
                children: [
                  for (final goal in value.goals)
                    ListTile(
                      leading: Icon(Icons.flag_outlined, color: priorityColor(goal.priority, colors)),
                      title: Text(goal.name),
                      trailing: Tag(label: humanise(goal.priority.name), color: priorityColor(goal.priority, colors)),
                    ),
                ],
              ),
              const SectionHeader('Constraints'),
              _Group(
                empty: 'Nothing limiting noted.',
                children: [
                  for (final item in value.constraints)
                    ListTile(
                      leading: Icon(Icons.block_outlined, color: Colors.orange.shade700),
                      title: Text(item),
                    ),
                ],
              ),
              const SectionHeader('Preferences'),
              _Group(
                empty: 'No preferences noted.',
                children: [
                  for (final entry in value.preferences.entries)
                    ListTile(
                      leading: Icon(Icons.favorite_border, color: Colors.pink.shade400),
                      title: Text(entry.value),
                      subtitle: Text(humanise(entry.key)),
                    ),
                ],
              ),
              const SectionHeader('More'),
              _Group(
                empty: '',
                children: [
                  ListTile(
                    leading: const Icon(Icons.public),
                    title: const Text('Time zone'),
                    trailing: Text(value.timezone, style: TextStyle(color: colors.onSurfaceVariant)),
                  ),
                  ListTile(
                    leading: const Icon(Icons.insights_outlined),
                    title: const Text('Your analysis'),
                    subtitle: const Text('What stands out from everything known about you'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => context.push('/profile/analysis'),
                  ),
                  ListTile(
                    leading: const Icon(Icons.help_outline),
                    title: const Text('How it works'),
                    subtitle: const Text('What makes this different from a chatbot'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => showHowItWorks(context),
                  ),
                ],
              ),
              const SectionHeader('Privacy & data'),
              _Group(
                empty: '',
                children: [
                  ListTile(
                    leading: const Icon(Icons.psychology_outlined),
                    title: const Text('Review what I remember'),
                    subtitle: const Text('Correct, archive or delete any memory'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => context.go('/memories'),
                  ),
                  ListTile(
                    leading: const Icon(Icons.logout),
                    title: const Text('Sign out'),
                    onTap: () => _signOut(context, ref),
                  ),
                  ListTile(
                    key: const Key('delete-account'),
                    leading: Icon(Icons.delete_forever_outlined, color: colors.error),
                    title: Text('Delete account', style: TextStyle(color: colors.error)),
                    subtitle: const Text('Permanently remove everything'),
                    onTap: () => showDeleteAccountDialog(context),
                  ),
                ],
              ),
            ],
          ),
        ),
        AsyncError(:final error) => ErrorView(error: error, onRetry: () => ref.invalidate(profileProvider)),
        _ => const Center(child: CircularProgressIndicator()),
      },
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.profile});

  final Profile profile;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    Widget stat(int count, String label) => Expanded(
      child: Column(
        children: [
          Text('$count', style: theme.textTheme.headlineSmall?.copyWith(color: colors.onPrimary)),
          Text(label, style: theme.textTheme.labelMedium?.copyWith(color: colors.onPrimary.withValues(alpha: 0.85))),
        ],
      ),
    );

    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(24),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [colors.primary, colors.tertiary],
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              CircleAvatar(
                radius: 26,
                backgroundColor: colors.onPrimary.withValues(alpha: 0.2),
                child: Icon(Icons.person, color: colors.onPrimary, size: 30),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Your profile', style: theme.textTheme.titleLarge?.copyWith(color: colors.onPrimary)),
                    Text(
                      profile.isEmpty ? 'Still taking shape' : 'What shapes every answer',
                      style: theme.textTheme.bodyMedium?.copyWith(color: colors.onPrimary.withValues(alpha: 0.85)),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 20),
          Row(
            children: [
              stat(profile.goals.length, 'Goals'),
              stat(profile.constraints.length, 'Constraints'),
              stat(profile.preferences.length, 'Preferences'),
            ],
          ),
        ],
      ),
    );
  }
}

/// A card of rows, or a quiet line when there is nothing to show.
class _Group extends StatelessWidget {
  const _Group({required this.empty, required this.children});

  final String empty;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (children.isEmpty) {
      return Padding(
        padding: const EdgeInsets.symmetric(horizontal: 4),
        child: Text(empty, style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
      );
    }
    return Card(
      clipBehavior: Clip.antiAlias,
      child: Column(
        children: [
          for (final (index, child) in children.indexed) ...[if (index > 0) const Divider(indent: 56), child],
        ],
      ),
    );
  }
}
