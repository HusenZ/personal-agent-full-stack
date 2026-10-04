import 'package:device_calendar_plus/device_calendar_plus.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'plan_repository.dart';

/// A calendar on the device that events can be written to.
class DeviceCalendarChoice {
  const DeviceCalendarChoice({required this.id, required this.name, this.account, this.colorHex, this.primary = false});

  final String id;
  final String name;
  final String? account;
  final String? colorHex;
  final bool primary;
}

/// One event to write, ready for the device calendar.
class CalendarEventDraft {
  const CalendarEventDraft({
    required this.blockId,
    required this.title,
    required this.start,
    required this.end,
    required this.allDay,
    required this.description,
    required this.timeZone,
    this.reminder,
  });

  final String blockId;
  final String title;
  final DateTime start;
  final DateTime end;
  final bool allDay;
  final String description;
  final String timeZone;
  final Duration? reminder;
}

enum CalendarAccess { granted, denied }

/// The device calendar, behind the few operations export needs, so the
/// export can be tested without a phone.
abstract interface class CalendarGateway {
  Future<CalendarAccess> requestAccess();

  /// Calendars the user can write to, the primary one first.
  Future<List<DeviceCalendarChoice>> writableCalendars();

  /// The text of events already in [calendarId] between [from] and [to].
  Future<List<String>> descriptionsBetween(String calendarId, DateTime from, DateTime to);

  Future<void> create(String calendarId, CalendarEventDraft draft);

  Future<void> openSettings();
}

/// The tag written into each exported event. Finding it again is how a
/// second export knows a block is already on the calendar, without the app
/// keeping its own record of what it wrote.
String exportMarker(String blockId) => 'pi-block:$blockId';

/// The events for [blocks]. Timed blocks become timed events at the instants
/// the server resolved; any-time blocks become all-day events only when asked.
List<CalendarEventDraft> draftsFor(
  WeekPlan plan,
  Iterable<PlanBlock> blocks, {
  required bool includeAnytime,
  Duration? reminder,
}) => [
  for (final block in blocks)
    if (block.startsAt != null && block.endsAt != null)
      CalendarEventDraft(
        blockId: block.id,
        title: block.title,
        start: block.startsAt!,
        end: block.endsAt!,
        allDay: false,
        description: _description(plan, block),
        timeZone: plan.timezone,
        reminder: reminder,
      )
    else if (includeAnytime)
      CalendarEventDraft(
        blockId: block.id,
        title: block.title,
        start: block.date,
        end: block.date.add(const Duration(days: 1)),
        allDay: true,
        description: _description(plan, block),
        timeZone: plan.timezone,
      ),
];

/// Kept to one line people might actually read, plus the marker.
String _description(WeekPlan plan, PlanBlock block) =>
    [if (block.reason.isNotEmpty) block.reason, exportMarker(block.id)].join('\n');

class ExportResult {
  const ExportResult({required this.added, required this.alreadyThere});

  final int added;
  final int alreadyThere;
}

/// Writes [drafts] to [calendarId], skipping any already there.
///
/// Stops at the first failure and reports it to the caller; events already
/// written stay, and a retry skips them.
Future<ExportResult> exportDrafts(CalendarGateway gateway, String calendarId, List<CalendarEventDraft> drafts) async {
  if (drafts.isEmpty) {
    return const ExportResult(added: 0, alreadyThere: 0);
  }
  final from = drafts.map((draft) => draft.start).reduce((a, b) => a.isBefore(b) ? a : b);
  final to = drafts.map((draft) => draft.end).reduce((a, b) => a.isAfter(b) ? a : b);
  final existing = await gateway.descriptionsBetween(
    calendarId,
    from.subtract(const Duration(days: 1)),
    to.add(const Duration(days: 1)),
  );

  var added = 0;
  var alreadyThere = 0;
  for (final draft in drafts) {
    final marker = exportMarker(draft.blockId);
    if (existing.any((description) => description.contains(marker))) {
      alreadyThere++;
      continue;
    }
    await gateway.create(calendarId, draft);
    added++;
  }
  return ExportResult(added: added, alreadyThere: alreadyThere);
}

class DeviceCalendarGateway implements CalendarGateway {
  DeviceCalendar get _plugin => DeviceCalendar.instance;

  @override
  Future<CalendarAccess> requestAccess() async {
    final status = await _plugin.requestPermissions();
    return status == CalendarPermissionStatus.granted ? CalendarAccess.granted : CalendarAccess.denied;
  }

  @override
  Future<List<DeviceCalendarChoice>> writableCalendars() async {
    final calendars = await _plugin.listCalendars();
    final writable = [
      for (final calendar in calendars)
        if (!calendar.readOnly && !calendar.hidden)
          DeviceCalendarChoice(
            id: calendar.id,
            name: calendar.name,
            account: calendar.accountName,
            colorHex: calendar.colorHex,
            primary: calendar.isPrimary,
          ),
    ]..sort((a, b) => (b.primary ? 1 : 0) - (a.primary ? 1 : 0));
    return writable;
  }

  @override
  Future<List<String>> descriptionsBetween(String calendarId, DateTime from, DateTime to) async {
    final events = await _plugin.listEvents(from, to, calendarIds: [calendarId]);
    return [for (final event in events) ?event.description];
  }

  @override
  Future<void> create(String calendarId, CalendarEventDraft draft) => _plugin.createEvent(
    calendarId: calendarId,
    title: draft.title,
    startDate: draft.start,
    endDate: draft.end,
    isAllDay: draft.allDay,
    description: draft.description,
    timeZone: draft.allDay ? null : draft.timeZone,
    reminders: draft.reminder == null ? null : [draft.reminder!],
  );

  @override
  Future<void> openSettings() => _plugin.openAppSettings();
}

final calendarGatewayProvider = Provider<CalendarGateway>((ref) => DeviceCalendarGateway());
