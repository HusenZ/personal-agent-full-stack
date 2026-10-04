import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/api/api_exception.dart';
import '../../core/ui/feedback.dart';
import 'add_to_calendar_sheet.dart';
import 'plan_repository.dart';

/// How each kind of block looks on the calendar.
(IconData, Color, String) blockLook(BlockCategory category, ColorScheme colors) => switch (category) {
  BlockCategory.goal => (Icons.flag_outlined, colors.primary, 'Goal'),
  BlockCategory.habit => (Icons.repeat, Colors.teal.shade600, 'Habit'),
  BlockCategory.health => (Icons.directions_run, Colors.green.shade600, 'Health'),
  BlockCategory.learning => (Icons.school_outlined, Colors.indigo.shade400, 'Learning'),
  BlockCategory.admin => (Icons.checklist, Colors.blueGrey.shade500, 'Admin'),
  BlockCategory.social => (Icons.people_outline, Colors.pink.shade400, 'Social'),
  BlockCategory.rest => (Icons.self_improvement, Colors.orange.shade700, 'Rest'),
  BlockCategory.other => (Icons.event_note, colors.outline, 'Other'),
};

String durationLabel(int minutes) {
  if (minutes < 60) {
    return '$minutes min';
  }
  final hours = minutes ~/ 60;
  final rest = minutes % 60;
  return rest == 0 ? '$hours h' : '$hours h $rest min';
}

/// "18:30" plus a duration → "19:10".
String endTime(String start, int minutes) {
  final parts = start.split(':').map(int.parse).toList();
  final total = parts[0] * 60 + parts[1] + minutes;
  return '${(total ~/ 60 % 24).toString().padLeft(2, '0')}:${(total % 60).toString().padLeft(2, '0')}';
}

const _weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

String rangeLabel(DateTime start, DateTime end) => start.month == end.month
    ? '${start.day} – ${end.day} ${_months[end.month - 1]}'
    : '${start.day} ${_months[start.month - 1]} – ${end.day} ${_months[end.month - 1]}';

/// The week's plan as a calendar: a strip of days, and the chosen day's
/// blocks in time order, each saying why it is there.
class WeekPlanScreen extends ConsumerStatefulWidget {
  const WeekPlanScreen({super.key});

  @override
  ConsumerState<WeekPlanScreen> createState() => _WeekPlanScreenState();
}

class _WeekPlanScreenState extends ConsumerState<WeekPlanScreen> {
  DateTime? _selected;
  bool _generating = false;

  Future<void> _generate({bool replacing = false}) async {
    if (replacing &&
        !await confirm(
          context,
          title: 'Make a new plan?',
          message: 'This plans the next seven days again from what I know now. Your current plan is kept in history.',
          action: 'Re-plan',
        )) {
      return;
    }
    setState(() => _generating = true);
    try {
      await ref.read(weekPlanProvider.notifier).generate();
      if (mounted) {
        setState(() => _selected = null);
      }
    } on ApiException catch (error) {
      if (mounted) {
        _explain(error);
      }
    } on Object catch (error) {
      if (mounted) {
        showError(context, error);
      }
    } finally {
      if (mounted) {
        setState(() => _generating = false);
      }
    }
  }

  void _explain(ApiException error) {
    if (error.code != 'INSUFFICIENT_CONTEXT') {
      showError(context, error);
      return;
    }
    showMessage(
      context,
      'I need to know your goals first.',
      actionLabel: 'Tell me',
      onAction: () => context.push('/onboarding'),
    );
  }

  Future<void> _toggle(PlanBlock block) async {
    unawaited(HapticFeedback.selectionClick());
    try {
      await ref.read(weekPlanProvider.notifier).toggle(block);
    } on Object catch (error) {
      if (mounted) {
        showError(context, error);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final plan = ref.watch(weekPlanProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Your week'),
        actions: [
          if (plan.value case final WeekPlan value)
            IconButton(
              key: const Key('add-to-calendar'),
              tooltip: 'Add to my calendar',
              onPressed: _generating
                  ? null
                  : () => showAddToCalendar(context, plan: value, selectedDay: _selected ?? _initialDay(value)),
              icon: const Icon(Icons.event_available_outlined),
            ),
          if (plan.value != null)
            IconButton(
              key: const Key('replan'),
              tooltip: 'Make a new plan',
              onPressed: _generating ? null : () => _generate(replacing: true),
              icon: const Icon(Icons.autorenew),
            ),
        ],
      ),
      body: switch (plan) {
        _ when _generating => const _Generating(),
        AsyncData(value: null) => _NoPlan(onGenerate: _generate),
        AsyncData(value: final WeekPlan value) => _Calendar(
          plan: value,
          selected: _selected ?? _initialDay(value),
          onSelect: (day) => setState(() => _selected = day),
          onToggle: _toggle,
          onAddToCalendar: () => showAddToCalendar(context, plan: value, selectedDay: _selected ?? _initialDay(value)),
        ),
        AsyncError(:final error) => ErrorView(error: error, onRetry: () => ref.invalidate(weekPlanProvider)),
        _ => const Center(child: CircularProgressIndicator()),
      },
    );
  }

  /// Today if it is in the plan, otherwise the plan's first day.
  DateTime _initialDay(WeekPlan plan) {
    final today = DateTime.now();
    return plan.days.firstWhere((day) => sameDate(day, today), orElse: () => plan.startsOn);
  }
}

class _Calendar extends StatelessWidget {
  const _Calendar({
    required this.plan,
    required this.selected,
    required this.onSelect,
    required this.onToggle,
    required this.onAddToCalendar,
  });

  final WeekPlan plan;
  final DateTime selected;
  final ValueChanged<DateTime> onSelect;
  final ValueChanged<PlanBlock> onToggle;
  final VoidCallback onAddToCalendar;

  @override
  Widget build(BuildContext context) {
    final blocks = plan.on(selected);
    return CustomScrollView(
      slivers: [
        SliverToBoxAdapter(
          child: _Overview(plan: plan, onAddToCalendar: onAddToCalendar),
        ),
        SliverToBoxAdapter(
          child: _WeekStrip(plan: plan, selected: selected, onSelect: onSelect),
        ),
        SliverToBoxAdapter(
          child: _DayHeader(day: selected, blocks: blocks),
        ),
        if (blocks.isEmpty)
          const SliverToBoxAdapter(child: _FreeDay())
        else
          SliverPadding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
            sliver: SliverToBoxAdapter(
              child: Card(
                clipBehavior: Clip.antiAlias,
                child: Column(
                  children: [
                    for (final (index, block) in blocks.indexed) ...[
                      if (index > 0) const Divider(indent: 60),
                      _ChecklistRow(
                        key: ValueKey(block.id),
                        block: block,
                        evidence: plan.evidence,
                        onToggle: () => onToggle(block),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          ),
      ],
    );
  }
}

class _Overview extends StatelessWidget {
  const _Overview({required this.plan, required this.onAddToCalendar});

  final WeekPlan plan;
  final VoidCallback onAddToCalendar;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final total = plan.blocks.length;
    final minutes = plan.blocks.fold<int>(0, (sum, block) => sum + block.durationMinutes);
    return Container(
      margin: const EdgeInsets.fromLTRB(16, 4, 16, 12),
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
          Row(
            children: [
              Icon(Icons.calendar_month, size: 18, color: colors.onPrimary),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  rangeLabel(plan.startsOn, plan.endsOn),
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.titleMedium?.copyWith(color: colors.onPrimary),
                ),
              ),
              const SizedBox(width: 8),
              Flexible(
                child: Text(
                  '${durationLabel(minutes)} planned',
                  overflow: TextOverflow.ellipsis,
                  textAlign: TextAlign.end,
                  style: theme.textTheme.labelMedium?.copyWith(color: colors.onPrimary.withValues(alpha: 0.85)),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            plan.summary,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.bodyMedium?.copyWith(color: colors.onPrimary, height: 1.4),
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: TweenAnimationBuilder<double>(
                    tween: Tween(end: total == 0 ? 0 : plan.doneCount / total),
                    duration: const Duration(milliseconds: 400),
                    builder: (context, value, _) => LinearProgressIndicator(
                      value: value,
                      minHeight: 6,
                      color: colors.onPrimary,
                      backgroundColor: colors.onPrimary.withValues(alpha: 0.25),
                      semanticsLabel: '${plan.doneCount} of $total done',
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Text(
                '${plan.doneCount} of $total done',
                key: const Key('plan-progress'),
                style: theme.textTheme.labelMedium?.copyWith(color: colors.onPrimary),
              ),
            ],
          ),
          const SizedBox(height: 14),
          Material(
            color: colors.onPrimary,
            shape: const StadiumBorder(),
            child: InkWell(
              key: const Key('overview-add-to-calendar'),
              customBorder: const StadiumBorder(),
              onTap: onAddToCalendar,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.event_available, size: 18, color: colors.primary),
                    const SizedBox(width: 8),
                    Text('Add to my calendar', style: theme.textTheme.labelLarge?.copyWith(color: colors.primary)),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _WeekStrip extends StatelessWidget {
  const _WeekStrip({required this.plan, required this.selected, required this.onSelect});

  final WeekPlan plan;
  final DateTime selected;
  final ValueChanged<DateTime> onSelect;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final today = DateTime.now();
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      child: Row(
        children: [
          for (final day in plan.days)
            Expanded(
              child: Builder(
                builder: (context) {
                  final isSelected = sameDate(day, selected);
                  final isToday = sameDate(day, today);
                  final blocks = plan.on(day);
                  final allDone = blocks.isNotEmpty && blocks.every((block) => block.done);
                  return Semantics(
                    selected: isSelected,
                    button: true,
                    label: '${_weekdays[day.weekday - 1]} ${day.day}, ${blocks.length} planned',
                    child: GestureDetector(
                      key: Key('day-${day.day}'),
                      onTap: () => onSelect(day),
                      child: AnimatedContainer(
                        duration: const Duration(milliseconds: 200),
                        margin: const EdgeInsets.symmetric(horizontal: 3),
                        padding: const EdgeInsets.symmetric(vertical: 10),
                        decoration: BoxDecoration(
                          color: isSelected ? colors.primary : colors.surfaceContainerHigh,
                          borderRadius: BorderRadius.circular(16),
                          border: isToday && !isSelected ? Border.all(color: colors.primary, width: 1.5) : null,
                        ),
                        child: ExcludeSemantics(
                          child: Column(
                            children: [
                              Text(
                                _weekdays[day.weekday - 1],
                                style: theme.textTheme.labelSmall?.copyWith(
                                  color: isSelected ? colors.onPrimary : colors.onSurfaceVariant,
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                '${day.day}',
                                style: theme.textTheme.titleMedium?.copyWith(
                                  color: isSelected ? colors.onPrimary : colors.onSurface,
                                ),
                              ),
                              const SizedBox(height: 6),
                              SizedBox(
                                height: 6,
                                child: allDone
                                    ? Icon(
                                        Icons.check,
                                        size: 8,
                                        color: isSelected ? colors.onPrimary : Colors.green.shade600,
                                      )
                                    : Row(
                                        mainAxisAlignment: MainAxisAlignment.center,
                                        children: [
                                          for (final block in blocks.take(3))
                                            Container(
                                              width: 5,
                                              height: 5,
                                              margin: const EdgeInsets.symmetric(horizontal: 1),
                                              decoration: BoxDecoration(
                                                shape: BoxShape.circle,
                                                color: isSelected
                                                    ? colors.onPrimary
                                                    : blockLook(block.category, colors).$2,
                                              ),
                                            ),
                                        ],
                                      ),
                              ),
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
      ),
    );
  }
}

class _DayHeader extends StatelessWidget {
  const _DayHeader({required this.day, required this.blocks});

  final DateTime day;
  final List<PlanBlock> blocks;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final today = sameDate(day, DateTime.now());
    final minutes = blocks.fold<int>(0, (sum, block) => sum + block.durationMinutes);
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 20, 20, 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: Text(
              today ? 'Today' : '${_fullWeekdays[day.weekday - 1]}, ${day.day} ${_months[day.month - 1]}',
              style: theme.textTheme.titleLarge,
            ),
          ),
          if (blocks.isNotEmpty)
            Text(
              '${blocks.length} ${blocks.length == 1 ? 'block' : 'blocks'} · ${durationLabel(minutes)}',
              style: theme.textTheme.labelMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
        ],
      ),
    );
  }
}

const _fullWeekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

/// One block as a checklist row: tick, title, when. Why it is there stays
/// folded away until asked for, because most people only scan the list.
class _ChecklistRow extends StatefulWidget {
  const _ChecklistRow({super.key, required this.block, required this.evidence, required this.onToggle});

  final PlanBlock block;
  final Map<String, String> evidence;
  final VoidCallback onToggle;

  @override
  State<_ChecklistRow> createState() => _ChecklistRowState();
}

class _ChecklistRowState extends State<_ChecklistRow> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final block = widget.block;
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final (icon, tone, _) = blockLook(block.category, colors);
    final serves = [for (final id in block.evidenceIds) ?widget.evidence[id]];
    final when = block.start == null
        ? 'Any time · ${durationLabel(block.durationMinutes)}'
        : '${block.start} · ${durationLabel(block.durationMinutes)}';

    return InkWell(
      key: Key('block-${block.id}'),
      onTap: () => setState(() => _open = !_open),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(4, 6, 12, 6),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Checkbox(
                  key: Key('done-${block.id}'),
                  value: block.done,
                  shape: const CircleBorder(),
                  activeColor: tone,
                  side: BorderSide(color: tone, width: 2),
                  onChanged: (_) => widget.onToggle(),
                  semanticLabel: block.done ? 'Mark ${block.title} not done' : 'Mark ${block.title} done',
                ),
                const SizedBox(width: 4),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        block.title,
                        maxLines: _open ? null : 1,
                        overflow: _open ? null : TextOverflow.ellipsis,
                        style: theme.textTheme.titleSmall?.copyWith(
                          decoration: block.done ? TextDecoration.lineThrough : null,
                          color: block.done ? colors.onSurfaceVariant : null,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Row(
                        children: [
                          Icon(icon, size: 13, color: tone),
                          const SizedBox(width: 4),
                          Text(when, style: theme.textTheme.labelSmall?.copyWith(color: colors.onSurfaceVariant)),
                        ],
                      ),
                    ],
                  ),
                ),
                Icon(
                  _open ? Icons.expand_less : Icons.expand_more,
                  size: 20,
                  color: colors.onSurfaceVariant,
                  semanticLabel: _open ? 'Hide why' : 'Show why',
                ),
              ],
            ),
            AnimatedSize(
              duration: const Duration(milliseconds: 180),
              alignment: Alignment.topCenter,
              child: !_open
                  ? const SizedBox(width: double.infinity)
                  : Padding(
                      padding: const EdgeInsets.fromLTRB(52, 4, 8, 8),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          if (block.reason.isNotEmpty)
                            Text(
                              block.reason,
                              key: Key('reason-${block.id}'),
                              style: theme.textTheme.bodySmall?.copyWith(color: colors.onSurfaceVariant),
                            ),
                          for (final item in serves)
                            Padding(
                              padding: const EdgeInsets.only(top: 4),
                              child: Row(
                                children: [
                                  Icon(Icons.link, size: 13, color: colors.primary),
                                  const SizedBox(width: 4),
                                  Expanded(
                                    child: Text(
                                      item,
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: theme.textTheme.labelSmall?.copyWith(color: colors.primary),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                        ],
                      ),
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

class _FreeDay extends StatelessWidget {
  const _FreeDay();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Row(
            children: [
              Icon(Icons.wb_twilight, color: Colors.orange.shade700),
              const SizedBox(width: 14),
              Expanded(
                child: Text(
                  'Nothing planned. Rest is part of the plan.',
                  style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _NoPlan extends StatelessWidget {
  const _NoPlan({required this.onGenerate});

  final VoidCallback onGenerate;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    Widget point(IconData icon, String text) => Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        children: [
          Icon(icon, size: 20, color: colors.primary),
          const SizedBox(width: 12),
          Expanded(child: Text(text, style: theme.textTheme.bodyMedium)),
        ],
      ),
    );
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(28),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Center(child: _CalendarArt()),
              const SizedBox(height: 28),
              Text('Plan your next seven days', style: theme.textTheme.headlineSmall),
              const SizedBox(height: 8),
              Text(
                'A realistic week built from your goals, not a template.',
                style: theme.textTheme.bodyLarge?.copyWith(color: colors.onSurfaceVariant),
              ),
              const SizedBox(height: 24),
              point(Icons.flag_outlined, 'Serves your goals, most important first'),
              point(Icons.block_outlined, 'Fits around your constraints and preferences'),
              point(Icons.link, 'Every block says why it is there'),
              point(Icons.check_circle_outline, 'Tick blocks off as the week goes'),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  key: const Key('generate-plan'),
                  onPressed: onGenerate,
                  icon: const Icon(Icons.auto_awesome),
                  label: const Text('Plan my week'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Generating extends StatelessWidget {
  const _Generating();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const _CalendarArt(pulsing: true),
            const SizedBox(height: 28),
            Text('Planning your week…', style: theme.textTheme.titleLarge, key: const Key('plan-generating')),
            const SizedBox(height: 8),
            Text(
              'Working from your goals and around your constraints. This can take a few seconds.',
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
          ],
        ),
      ),
    );
  }
}

/// A small drawn calendar: seven columns with blocks, for empty and loading
/// states.
class _CalendarArt extends StatefulWidget {
  const _CalendarArt({this.pulsing = false});

  final bool pulsing;

  @override
  State<_CalendarArt> createState() => _CalendarArtState();
}

class _CalendarArtState extends State<_CalendarArt> with SingleTickerProviderStateMixin {
  late final _controller = AnimationController(vsync: this, duration: const Duration(milliseconds: 1400));

  static const _heights = [
    [18.0, 10.0],
    [10.0],
    [22.0, 8.0],
    [12.0, 12.0],
    [24.0],
    [8.0],
    <double>[],
  ];

  @override
  void initState() {
    super.initState();
    if (widget.pulsing) {
      _controller.repeat();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final tones = [colors.primary, Colors.green.shade600, Colors.orange.shade700, Colors.indigo.shade400];
    return ExcludeSemantics(
      child: Container(
        width: 200,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(color: colors.surfaceContainerHigh, borderRadius: BorderRadius.circular(22)),
        child: AnimatedBuilder(
          animation: _controller,
          builder: (context, _) => Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (var column = 0; column < 7; column++)
                Expanded(
                  child: Opacity(
                    // Columns light up in turn while a plan is being made.
                    opacity: widget.pulsing ? (((_controller.value * 7) - column).abs() < 1 ? 1 : 0.35) : 1,
                    child: Column(
                      children: [
                        Container(
                          height: 6,
                          margin: const EdgeInsets.symmetric(horizontal: 3),
                          decoration: BoxDecoration(
                            color: colors.outlineVariant,
                            borderRadius: BorderRadius.circular(3),
                          ),
                        ),
                        const SizedBox(height: 8),
                        SizedBox(
                          height: 64,
                          child: Column(
                            children: [
                              for (final (index, height) in _heights[column].indexed)
                                Container(
                                  height: height,
                                  margin: const EdgeInsets.fromLTRB(3, 0, 3, 4),
                                  decoration: BoxDecoration(
                                    color: tones[(column + index) % tones.length].withValues(alpha: 0.75),
                                    borderRadius: BorderRadius.circular(4),
                                  ),
                                ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
