import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/ui/components.dart';
import '../../core/ui/feedback.dart';
import '../../core/ui/formatting.dart';
import '../analysis/analysis_screen.dart';
import '../chat/chat_repository.dart';
import '../chat/conversations_controller.dart';
import '../memories/memories_repository.dart';
import '../memories/memories_screen.dart' show memoryLook;
import '../onboarding/onboarding_repository.dart';
import '../profile/profile_repository.dart';
import '../planning/plan_repository.dart';
import '../planning/week_plan_screen.dart' show blockLook, durationLabel;
import '../profile/profile_screen.dart' show priorityColor;

/// The newest active memories, and how many there are in all.
final recentMemoriesProvider = FutureProvider.autoDispose<PageOf<Memory>>(
  (ref) => ref.watch(memoriesRepositoryProvider).list(includeArchived: false),
);

/// What a suggestion turns into when tapped: a draft the user can edit
/// before sending, never a message sent on their behalf.
String draftAbout(String point) => 'Help me act on this: $point';

String draftForGoal(String goal) => 'How am I doing on my goal to ${goal.toLowerCase()}? What should I do next?';

/// The home tab: what the assistant knows, what it suggests, and a way in.
///
/// Every section loads on its own, so one failing request leaves the rest of
/// the page useful instead of replacing it with an error.
class TodayScreen extends ConsumerWidget {
  const TodayScreen({super.key});

  Future<void> _refresh(WidgetRef ref) async {
    ref
      ..invalidate(profileProvider)
      ..invalidate(analysisProvider)
      ..invalidate(recentMemoriesProvider)
      ..invalidate(onboardingStatusProvider)
      ..invalidate(conversationsProvider)
      ..invalidate(weekPlanProvider);
    await ref.read(profileProvider.future);
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final now = DateTime.now();
    final theme = Theme.of(context);

    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: RefreshIndicator(
          onRefresh: () => _refresh(ref),
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 4),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      longDate(now).toUpperCase(),
                      style: theme.textTheme.labelMedium?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                        letterSpacing: 1.1,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(greetingFor(now), style: theme.textTheme.headlineMedium),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              const _AskCard(),
              const SizedBox(height: 12),
              const _Understanding(),
              const _ThisWeek(),
              const _Focus(),
              const _Goals(),
              const _RecentlyLearned(),
              const _Continue(),
            ],
          ),
        ),
      ),
    );
  }
}

/// Today's slice of the week plan, or an invitation to make one.
class _ThisWeek extends ConsumerWidget {
  const _ThisWeek();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final plan = ref.watch(weekPlanProvider);
    final theme = Theme.of(context);
    final colors = theme.colorScheme;

    return switch (plan) {
      AsyncData(value: final WeekPlan value) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionHeader(
            'Today in your plan',
            trailing: TextButton(onPressed: () => context.push('/plan'), child: const Text('Open week')),
          ),
          Card(
            clipBehavior: Clip.antiAlias,
            child: Column(
              children: [
                if (value.on(DateTime.now()).isEmpty)
                  ListTile(
                    leading: Icon(Icons.wb_twilight, color: Colors.orange.shade700),
                    title: const Text('Nothing planned today'),
                    subtitle: const Text('Rest is part of the plan.'),
                    onTap: () => context.push('/plan'),
                  ),
                for (final (index, block) in value.on(DateTime.now()).take(3).indexed) ...[
                  if (index > 0) const Divider(indent: 56),
                  CheckboxListTile(
                    key: Key('today-block-${block.id}'),
                    controlAffinity: ListTileControlAffinity.leading,
                    checkboxShape: const CircleBorder(),
                    activeColor: blockLook(block.category, colors).$2,
                    value: block.done,
                    onChanged: (_) async {
                      try {
                        await ref.read(weekPlanProvider.notifier).toggle(block);
                      } on Object catch (error) {
                        if (context.mounted) {
                          showError(context, error);
                        }
                      }
                    },
                    title: Text(
                      block.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(decoration: block.done ? TextDecoration.lineThrough : null),
                    ),
                    subtitle: Text('${block.start ?? 'Any time'} · ${durationLabel(block.durationMinutes)}'),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
      AsyncData() => Padding(
        padding: const EdgeInsets.only(top: 12),
        child: _Prompt(
          icon: Icons.calendar_month_outlined,
          title: 'Plan your week',
          body: 'Seven days built from your goals and around your constraints, on a calendar you can tick off.',
          action: 'Plan my week',
          onTap: () => context.push('/plan'),
        ),
      ),
      _ => const SizedBox.shrink(),
    };
  }
}

class _AskCard extends StatelessWidget {
  const _AskCard();

  // Each opens its own place: the week planner, or a chat draft.
  static const _quick = [
    ('Plan my week', Icons.calendar_month_outlined, '/plan'),
    ('Reflect on today', Icons.edit_note, null),
    ('What should I focus on?', Icons.center_focus_strong_outlined, null),
  ];

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return Container(
      padding: const EdgeInsets.all(18),
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
          Text("What's on your mind?", style: theme.textTheme.titleLarge?.copyWith(color: colors.onPrimary)),
          const SizedBox(height: 4),
          Text(
            'I answer from what you have told me, and say when I do not know.',
            style: theme.textTheme.bodyMedium?.copyWith(color: colors.onPrimary.withValues(alpha: 0.85)),
          ),
          const SizedBox(height: 16),
          Material(
            color: colors.onPrimary,
            borderRadius: BorderRadius.circular(28),
            child: InkWell(
              key: const Key('today-ask'),
              borderRadius: BorderRadius.circular(28),
              onTap: () => context.push('/chats/new'),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
                child: Row(
                  children: [
                    Icon(Icons.auto_awesome, color: colors.primary, size: 20),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        'Ask anything',
                        style: theme.textTheme.bodyLarge?.copyWith(color: colors.onSurfaceVariant),
                      ),
                    ),
                    Icon(Icons.arrow_forward, color: colors.primary, size: 20),
                  ],
                ),
              ),
            ),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final (label, icon, route) in _quick)
                // Custom-drawn: theme chip colours look wrong on this gradient.
                Material(
                  color: colors.onPrimary.withValues(alpha: 0.18),
                  shape: const StadiumBorder(),
                  child: InkWell(
                    customBorder: const StadiumBorder(),
                    onTap: () => route == null ? context.push('/chats/new', extra: label) : context.push(route),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(icon, size: 16, color: colors.onPrimary),
                          const SizedBox(width: 6),
                          Flexible(
                            child: Text(
                              label,
                              overflow: TextOverflow.ellipsis,
                              style: theme.textTheme.labelLarge?.copyWith(color: colors.onPrimary),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

/// How much the assistant knows, made visible, so "it gets better as you
/// talk" is something the user can watch happen.
class _Understanding extends ConsumerWidget {
  const _Understanding();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final onboarding = ref.watch(onboardingStatusProvider);
    final memories = ref.watch(recentMemoriesProvider).value;
    final profile = ref.watch(profileProvider).value;
    final conversations = ref.watch(conversationsProvider).value;

    final turn = onboarding.value;
    final confidence = (turn?.overallConfidence ?? 0).clamp(0.0, 1.0);
    final interviewDone = turn?.isComplete ?? false;

    Widget stat(String value, String label, IconData icon) => Expanded(
      child: Column(
        children: [
          Icon(icon, size: 20, color: colors.primary),
          const SizedBox(height: 6),
          Text(value, style: theme.textTheme.titleLarge),
          Text(label, style: theme.textTheme.labelSmall?.copyWith(color: colors.onSurfaceVariant)),
        ],
      ),
    );

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                SizedBox.square(
                  dimension: 52,
                  child: Stack(
                    fit: StackFit.expand,
                    children: [
                      CircularProgressIndicator(
                        value: onboarding.isLoading ? null : confidence,
                        strokeWidth: 5,
                        strokeCap: StrokeCap.round,
                        backgroundColor: colors.surfaceContainerHighest,
                      ),
                      Center(
                        child: Text(
                          '${(confidence * 100).round()}%',
                          style: theme.textTheme.labelLarge?.copyWith(fontWeight: FontWeight.w700),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('How well I know you', style: theme.textTheme.titleMedium),
                      const SizedBox(height: 2),
                      Text(
                        interviewDone
                            ? 'Every conversation adds to this.'
                            : 'A few more answers make every reply more personal.',
                        style: theme.textTheme.bodySmall?.copyWith(color: colors.onSurfaceVariant),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 18),
            Row(
              children: [
                stat('${memories?.count ?? '–'}', 'memories', Icons.psychology_outlined),
                stat('${profile?.goals.length ?? '–'}', 'goals', Icons.flag_outlined),
                stat(
                  '${conversations?.items.length ?? '–'}${conversations?.hasMore ?? false ? '+' : ''}',
                  'chats',
                  Icons.forum_outlined,
                ),
              ],
            ),
            if (onboarding.hasValue && !interviewDone) ...[
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: FilledButton.tonalIcon(
                  key: const Key('today-onboarding'),
                  style: FilledButton.styleFrom(minimumSize: const Size(0, 46)),
                  onPressed: () => context.push('/onboarding'),
                  icon: const Icon(Icons.chat_bubble_outline, size: 18),
                  label: const Text('Answer a few questions'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// The latest analysis's priorities and suggestions, each one a way into a
/// conversation about it.
class _Focus extends ConsumerWidget {
  const _Focus();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final analysis = ref.watch(analysisProvider);
    final colors = Theme.of(context).colorScheme;

    return switch (analysis) {
      AsyncData(value: final Analysis value) when _points(value).isNotEmpty => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionHeader(
            'Your focus',
            trailing: TextButton(
              onPressed: () => context.push('/profile/analysis'),
              child: const Text('Full analysis'),
            ),
          ),
          Card(
            clipBehavior: Clip.antiAlias,
            child: Column(
              children: [
                for (final (index, claim) in _points(value).indexed) ...[
                  if (index > 0) const Divider(indent: 16, endIndent: 16),
                  ListTile(
                    contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                    leading: Icon(claimLook(claim.type, colors).$1, color: claimLook(claim.type, colors).$2),
                    title: Text(claim.content),
                    subtitle: Padding(
                      padding: const EdgeInsets.only(top: 6),
                      child: Align(
                        alignment: Alignment.centerLeft,
                        child: Tag(label: humanise(claim.type), color: claimLook(claim.type, colors).$2),
                      ),
                    ),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => context.push('/chats/new', extra: draftAbout(claim.content)),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
      AsyncData() => Padding(
        padding: const EdgeInsets.only(top: 12),
        child: _Prompt(
          icon: Icons.insights_outlined,
          title: 'Get your first analysis',
          body: 'I read everything I know about you and say what stands out, marking facts apart from guesses.',
          action: 'Analyse',
          onTap: () => context.push('/profile/analysis'),
        ),
      ),
      _ => const SizedBox.shrink(),
    };
  }

  static List<Claim> _points(Analysis analysis) =>
      [...?analysis.sections['priorities'], ...?analysis.sections['suggested_actions']].take(3).toList();
}

class _Goals extends ConsumerWidget {
  const _Goals();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = ref.watch(profileProvider).value;
    if (profile == null) {
      return const SizedBox.shrink();
    }
    final colors = Theme.of(context).colorScheme;
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SectionHeader(
          'Your goals',
          trailing: TextButton(
            onPressed: () => context.push('/profile/edit', extra: profile),
            child: Text(profile.goals.isEmpty ? 'Add' : 'Edit'),
          ),
        ),
        if (profile.goals.isEmpty)
          _Prompt(
            icon: Icons.flag_outlined,
            title: 'What are you working towards?',
            body: 'Goals shape every answer: plans, priorities and check-ins all start from them.',
            action: 'Add a goal',
            onTap: () => context.push('/profile/edit', extra: profile),
          )
        else
          SizedBox(
            // Scales with text size so large accessibility text isn't clipped.
            height: 64 + 64 * MediaQuery.textScalerOf(context).scale(1),
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              itemCount: profile.goals.length,
              separatorBuilder: (_, _) => const SizedBox(width: 10),
              itemBuilder: (context, index) {
                final goal = profile.goals[index];
                final tone = priorityColor(goal.priority, colors);
                return SizedBox(
                  width: 200,
                  child: Card(
                    clipBehavior: Clip.antiAlias,
                    child: InkWell(
                      onTap: () => context.push('/chats/new', extra: draftForGoal(goal.name)),
                      child: Padding(
                        padding: const EdgeInsets.all(14),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Icon(Icons.flag, size: 18, color: tone),
                                const Spacer(),
                                Tag(label: humanise(goal.priority.name), color: tone),
                              ],
                            ),
                            const Spacer(),
                            Text(
                              goal.name,
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: theme.textTheme.titleSmall,
                            ),
                            const SizedBox(height: 2),
                            Text('Check in', style: theme.textTheme.labelSmall?.copyWith(color: colors.primary)),
                          ],
                        ),
                      ),
                    ),
                  ),
                );
              },
            ),
          ),
      ],
    );
  }
}

/// The newest memories: evidence that the assistant is learning, and a
/// reminder that the user can see and change what it keeps.
class _RecentlyLearned extends ConsumerWidget {
  const _RecentlyLearned();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final memories = ref.watch(recentMemoriesProvider).value;
    if (memories == null) {
      return const SizedBox.shrink();
    }
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SectionHeader(
          'Recently learned',
          trailing: memories.items.isEmpty
              ? null
              : TextButton(onPressed: () => context.go('/memories'), child: const Text('See all')),
        ),
        if (memories.items.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4),
            child: Text(
              'Nothing yet. Things worth keeping from your chats appear here, and you can correct or delete any of them.',
              style: theme.textTheme.bodyMedium?.copyWith(color: colors.onSurfaceVariant),
            ),
          )
        else
          Card(
            clipBehavior: Clip.antiAlias,
            child: Column(
              children: [
                for (final (index, memory) in memories.items.take(3).indexed) ...[
                  if (index > 0) const Divider(indent: 56),
                  ListTile(
                    leading: Icon(memoryLook(memory.type, colors).$1, color: memoryLook(memory.type, colors).$2),
                    title: Text(memory.content, maxLines: 2, overflow: TextOverflow.ellipsis),
                    subtitle: Text(
                      '${humanise(memory.type)} · ${formatTimestamp(memory.occurredAt ?? memory.createdAt)}',
                    ),
                    onTap: () => context.go('/memories'),
                  ),
                ],
              ],
            ),
          ),
      ],
    );
  }
}

class _Continue extends ConsumerWidget {
  const _Continue();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final latest = ref.watch(conversationsProvider).value?.items.firstOrNull;
    if (latest == null) {
      return const SizedBox.shrink();
    }
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SectionHeader('Pick up where you left off'),
        Card(
          clipBehavior: Clip.antiAlias,
          child: ListTile(
            contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            leading: const AssistantAvatar(size: 36),
            title: Text(latest.title, maxLines: 1, overflow: TextOverflow.ellipsis, style: theme.textTheme.titleSmall),
            subtitle: Text('${latest.messageCount} messages · ${formatTimestamp(latest.lastMessageAt)}'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => context.push('/chats/${latest.id}'),
          ),
        ),
      ],
    );
  }
}

class _Prompt extends StatelessWidget {
  const _Prompt({
    required this.icon,
    required this.title,
    required this.body,
    required this.action,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final String body;
  final String action;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, color: colors.primary),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: theme.textTheme.titleSmall),
                  const SizedBox(height: 4),
                  Text(body, style: theme.textTheme.bodySmall?.copyWith(color: colors.onSurfaceVariant, height: 1.4)),
                  const SizedBox(height: 10),
                  FilledButton.tonal(
                    style: FilledButton.styleFrom(minimumSize: const Size(0, 40)),
                    onPressed: onTap,
                    child: Text(action),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
