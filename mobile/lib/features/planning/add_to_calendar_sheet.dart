import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/ui/components.dart';
import 'calendar_export.dart';
import 'plan_repository.dart';

/// Which blocks to put on the device calendar.
enum ExportScope { day, rest, week }

/// Asks for calendar access, lets the user pick a calendar and a range, and
/// writes the plan's blocks there.
Future<void> showAddToCalendar(BuildContext context, {required WeekPlan plan, required DateTime selectedDay}) =>
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _AddToCalendarSheet(plan: plan, selectedDay: selectedDay),
    );

/// The blocks in [scope]: the chosen day, today onwards, or all seven days.
List<PlanBlock> blocksIn(WeekPlan plan, ExportScope scope, {required DateTime selectedDay, DateTime? now}) {
  final today = now ?? DateTime.now();
  final todayDate = DateTime(today.year, today.month, today.day);
  return switch (scope) {
    ExportScope.day => plan.on(selectedDay),
    ExportScope.rest => [
      for (final block in plan.blocks)
        if (!block.date.isBefore(todayDate)) block,
    ],
    ExportScope.week => plan.blocks,
  };
}

enum _Stage { asking, denied, choosing, adding, done, failed }

class _AddToCalendarSheet extends ConsumerStatefulWidget {
  const _AddToCalendarSheet({required this.plan, required this.selectedDay});

  final WeekPlan plan;
  final DateTime selectedDay;

  @override
  ConsumerState<_AddToCalendarSheet> createState() => _AddToCalendarSheetState();
}

class _AddToCalendarSheetState extends ConsumerState<_AddToCalendarSheet> {
  _Stage _stage = _Stage.asking;
  List<DeviceCalendarChoice> _calendars = const [];
  String? _calendarId;
  ExportScope _scope = ExportScope.rest;
  bool _remind = true;
  bool _includeAnytime = false;
  ExportResult? _result;
  String? _error;

  CalendarGateway get _gateway => ref.read(calendarGatewayProvider);

  @override
  void initState() {
    super.initState();
    _connect();
  }

  Future<void> _connect() async {
    setState(() => _stage = _Stage.asking);
    try {
      if (await _gateway.requestAccess() != CalendarAccess.granted) {
        if (mounted) {
          setState(() => _stage = _Stage.denied);
        }
        return;
      }
      final calendars = await _gateway.writableCalendars();
      if (mounted) {
        setState(() {
          _calendars = calendars;
          _calendarId = calendars.firstOrNull?.id;
          _stage = _Stage.choosing;
        });
      }
    } on Object {
      if (mounted) {
        setState(() {
          _stage = _Stage.failed;
          _error = "Couldn't open your calendars.";
        });
      }
    }
  }

  List<CalendarEventDraft> get _drafts => draftsFor(
    widget.plan,
    blocksIn(widget.plan, _scope, selectedDay: widget.selectedDay),
    includeAnytime: _includeAnytime,
    reminder: _remind ? const Duration(minutes: 10) : null,
  );

  Future<void> _add() async {
    final calendarId = _calendarId;
    if (calendarId == null) {
      return;
    }
    setState(() => _stage = _Stage.adding);
    try {
      final result = await exportDrafts(_gateway, calendarId, _drafts);
      if (mounted) {
        setState(() {
          _result = result;
          _stage = _Stage.done;
        });
      }
    } on Object {
      if (mounted) {
        setState(() {
          _stage = _Stage.failed;
          _error = "Some events couldn't be added. Try again; the ones already added won't be duplicated.";
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
        child: AnimatedSize(
          duration: const Duration(milliseconds: 200),
          alignment: Alignment.topCenter,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text('Add to my calendar', style: theme.textTheme.titleLarge),
              const SizedBox(height: 4),
              Text(
                'Puts your plan on the calendar you already use. Adding again never duplicates a block.',
                style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
              ),
              const SizedBox(height: 20),
              ...switch (_stage) {
                _Stage.asking || _Stage.adding => [
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 32),
                    child: Center(child: CircularProgressIndicator()),
                  ),
                ],
                _Stage.denied => _denied(context),
                _Stage.choosing => _choosing(context),
                _Stage.done => _done(context),
                _Stage.failed => _failed(context),
              },
            ],
          ),
        ),
      ),
    );
  }

  List<Widget> _denied(BuildContext context) => [
    const _Notice(
      icon: Icons.event_busy_outlined,
      title: 'Calendar access is off',
      body: 'Allow calendar access in Settings, then come back and try again.',
    ),
    const SizedBox(height: 16),
    FilledButton(onPressed: _gateway.openSettings, child: const Text('Open Settings')),
    TextButton(onPressed: _connect, child: const Text('Try again')),
  ];

  List<Widget> _failed(BuildContext context) => [
    _Notice(icon: Icons.error_outline, title: 'Something went wrong', body: _error ?? ''),
    const SizedBox(height: 16),
    FilledButton(onPressed: _calendars.isEmpty ? _connect : _add, child: const Text('Try again')),
  ];

  List<Widget> _done(BuildContext context) {
    final result = _result!;
    final calendar = _calendars.where((item) => item.id == _calendarId).firstOrNull?.name ?? 'your calendar';
    return [
      _Notice(
        icon: Icons.event_available,
        tone: Colors.green.shade600,
        title: result.added == 0
            ? 'Already on $calendar'
            : 'Added ${result.added} ${result.added == 1 ? 'event' : 'events'} to $calendar',
        body: result.alreadyThere == 0
            ? 'You will see them alongside everything else in your calendar.'
            : '${result.alreadyThere} ${result.alreadyThere == 1 ? 'was' : 'were'} already there, so '
                  '${result.alreadyThere == 1 ? 'it was' : 'they were'} skipped.',
      ),
      const SizedBox(height: 16),
      FilledButton(
        key: const Key('calendar-done'),
        onPressed: () => Navigator.of(context).pop(),
        child: const Text('Done'),
      ),
    ];
  }

  List<Widget> _choosing(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    if (_calendars.isEmpty) {
      return [
        const _Notice(
          icon: Icons.event_busy_outlined,
          title: 'No calendar to add to',
          body: 'There is no calendar on this device that can be written to. Add an account in your calendar app.',
        ),
      ];
    }
    final count = _drafts.length;
    return [
      SegmentedButton<ExportScope>(
        showSelectedIcon: false,
        segments: const [
          ButtonSegment(value: ExportScope.day, label: Text('This day')),
          ButtonSegment(value: ExportScope.rest, label: Text('Rest of week')),
          ButtonSegment(value: ExportScope.week, label: Text('All 7 days')),
        ],
        selected: {_scope},
        onSelectionChanged: (selection) => setState(() => _scope = selection.first),
      ),
      const SectionHeader('Calendar'),
      Card(
        clipBehavior: Clip.antiAlias,
        child: RadioGroup<String>(
          groupValue: _calendarId,
          onChanged: (value) => setState(() => _calendarId = value),
          child: Column(
            children: [
              for (final calendar in _calendars)
                RadioListTile<String>(
                  key: Key('calendar-${calendar.id}'),
                  value: calendar.id,
                  title: Text(calendar.name),
                  subtitle: calendar.account == null || calendar.account == calendar.name
                      ? null
                      : Text(calendar.account!),
                  secondary: Icon(Icons.circle, size: 14, color: _color(calendar.colorHex) ?? colors.primary),
                ),
            ],
          ),
        ),
      ),
      const SizedBox(height: 8),
      SwitchListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 4),
        title: const Text('Remind me 10 minutes before'),
        value: _remind,
        onChanged: (value) => setState(() => _remind = value),
      ),
      SwitchListTile(
        key: const Key('include-anytime'),
        contentPadding: const EdgeInsets.symmetric(horizontal: 4),
        title: const Text('Include "any time" blocks'),
        subtitle: const Text('As all-day events'),
        value: _includeAnytime,
        onChanged: (value) => setState(() => _includeAnytime = value),
      ),
      const SizedBox(height: 12),
      FilledButton.icon(
        key: const Key('calendar-add'),
        onPressed: count == 0 || _calendarId == null ? null : _add,
        icon: const Icon(Icons.event_available),
        label: Text(count == 0 ? 'Nothing to add' : 'Add $count ${count == 1 ? 'event' : 'events'}'),
      ),
    ];
  }

  Color? _color(String? hex) {
    if (hex == null || hex.length != 7) {
      return null;
    }
    final value = int.tryParse(hex.substring(1), radix: 16);
    return value == null ? null : Color(0xFF000000 | value);
  }
}

class _Notice extends StatelessWidget {
  const _Notice({required this.icon, required this.title, required this.body, this.tone});

  final IconData icon;
  final String title;
  final String body;
  final Color? tone;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final color = tone ?? theme.colorScheme.primary;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: 44,
          height: 44,
          decoration: BoxDecoration(color: color.withValues(alpha: 0.12), shape: BoxShape.circle),
          child: Icon(icon, color: color),
        ),
        const SizedBox(width: 14),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(title, key: const Key('calendar-notice'), style: theme.textTheme.titleMedium),
              const SizedBox(height: 4),
              Text(body, style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
            ],
          ),
        ),
      ],
    );
  }
}
