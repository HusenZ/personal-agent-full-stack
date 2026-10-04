import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:personal_intelligence/features/planning/add_to_calendar_sheet.dart';
import 'package:personal_intelligence/features/planning/calendar_export.dart';
import 'package:personal_intelligence/features/planning/plan_repository.dart';

String iso(DateTime day) => '${day.year}-${day.month.toString().padLeft(2, '0')}-${day.day.toString().padLeft(2, '0')}';

/// Yesterday, today and tomorrow: one block each, today's two (timed and any time).
WeekPlan plan() {
  final today = DateTime.now();
  DateTime day(int offset) => today.add(Duration(days: offset));
  Map<String, Object?> block(String id, int offset, {String? startsAt}) => {
    'id': id,
    'date': iso(day(offset)),
    'start': startsAt == null ? null : '19:00',
    'starts_at': startsAt,
    'ends_at': startsAt?.replaceFirst('T19:00', 'T19:40'),
    'duration_minutes': 40,
    'title': 'Block $id',
    'category': 'health',
    'reason': 'Builds your base.',
    'evidence_ids': ['goal:1'],
    'done': false,
  };
  return WeekPlan.fromJson({
    'id': 'p1',
    'starts_on': iso(day(-1)),
    'ends_on': iso(day(5)),
    'timezone': 'Asia/Kolkata',
    'summary': 's',
    'blocks': [
      block('past', -1, startsAt: '${iso(day(-1))}T19:00:00+05:30'),
      block('timed', 0, startsAt: '${iso(day(0))}T19:00:00+05:30'),
      block('anytime', 0),
      block('later', 1, startsAt: '${iso(day(1))}T19:00:00+05:30'),
    ],
    'evidence': [
      {'id': 'goal:1', 'kind': 'goal', 'content': 'Run a half marathon'},
    ],
  });
}

void main() {
  group('turning blocks into events', () {
    test('timed blocks keep the instant the server resolved, in the plan\'s zone', () {
      final drafts = draftsFor(plan(), plan().blocks.where((b) => b.id == 'timed'), includeAnytime: false);

      final draft = drafts.single;
      expect(draft.start.toUtc().hour, 13, reason: '19:00 in Kolkata is 13:30 UTC');
      expect(draft.start.toUtc().minute, 30);
      expect(draft.end.difference(draft.start), const Duration(minutes: 40));
      expect(draft.timeZone, 'Asia/Kolkata');
      expect(draft.allDay, isFalse);
    });

    test('the notes are one short line plus the marker', () {
      final draft = draftsFor(plan(), plan().blocks.take(1), includeAnytime: false).single;

      expect(draft.description, 'Builds your base.\n${exportMarker('past')}');
    });

    test('any-time blocks are left out unless asked for, then are all-day', () {
      final today = plan().on(DateTime.now());

      expect(draftsFor(plan(), today, includeAnytime: false).map((d) => d.blockId), ['timed']);
      final anytime = draftsFor(plan(), today, includeAnytime: true).last;
      expect(anytime.allDay, isTrue);
      expect(anytime.end.difference(anytime.start), const Duration(days: 1));
    });

    test('a range is the day, today onwards, or the whole week', () {
      final selected = DateTime.now();
      List<String> ids(ExportScope scope) => [for (final b in blocksIn(plan(), scope, selectedDay: selected)) b.id];

      expect(ids(ExportScope.day), ['timed', 'anytime']);
      expect(ids(ExportScope.rest), ['timed', 'anytime', 'later']);
      expect(ids(ExportScope.week), ['past', 'timed', 'anytime', 'later']);
    });
  });

  group('exporting', () {
    test('blocks already on the calendar are skipped, the rest added', () async {
      final calendar = _Calendar()..existing.add('Old note. ${exportMarker('timed')}');
      final drafts = draftsFor(plan(), plan().blocks, includeAnytime: false);

      final result = await exportDrafts(calendar, 'c1', drafts);

      expect(result.added, 2);
      expect(result.alreadyThere, 1);
      expect(calendar.created.map((d) => d.blockId), ['past', 'later']);
    });

    test('exporting twice adds nothing the second time', () async {
      final calendar = _Calendar();
      final drafts = draftsFor(plan(), plan().blocks, includeAnytime: true);

      await exportDrafts(calendar, 'c1', drafts);
      final again = await exportDrafts(calendar, 'c1', drafts);

      expect(again.added, 0);
      expect(again.alreadyThere, drafts.length);
    });
  });

  group('the sheet', () {
    Future<_Calendar> open(WidgetTester tester, _Calendar calendar) async {
      tester.view.physicalSize = const Size(1080, 2400);
      tester.view.devicePixelRatio = 2.75;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [calendarGatewayProvider.overrideWithValue(calendar)],
          child: MaterialApp(
            home: Builder(
              builder: (context) => TextButton(
                onPressed: () => showAddToCalendar(context, plan: plan(), selectedDay: DateTime.now()),
                child: const Text('go'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('go'));
      await tester.pumpAndSettle();
      return calendar;
    }

    testWidgets('adds the rest of the week to the chosen calendar', (tester) async {
      final calendar = await open(tester, _Calendar());

      expect(find.text('Add 2 events'), findsOneWidget, reason: 'today and tomorrow, timed only');
      await tester.tap(find.byKey(const Key('calendar-c2')));
      await tester.tap(find.byKey(const Key('calendar-add')));
      await tester.pumpAndSettle();

      expect(calendar.createdIn, ['c2', 'c2']);
      expect(calendar.created.first.reminder, const Duration(minutes: 10));
      expect(find.text('Added 2 events to Work'), findsOneWidget);
    });

    testWidgets('without access, it explains and offers Settings', (tester) async {
      final calendar = await open(tester, _Calendar(granted: false));

      expect(find.text('Calendar access is off'), findsOneWidget);
      await tester.tap(find.text('Open Settings'));
      expect(calendar.openedSettings, isTrue);
      expect(calendar.created, isEmpty);
    });
  });
}

class _Calendar implements CalendarGateway {
  _Calendar({this.granted = true});

  final bool granted;
  final existing = <String>[];
  final created = <CalendarEventDraft>[];
  final createdIn = <String>[];
  bool openedSettings = false;

  @override
  Future<CalendarAccess> requestAccess() async => granted ? CalendarAccess.granted : CalendarAccess.denied;

  @override
  Future<List<DeviceCalendarChoice>> writableCalendars() async => const [
    DeviceCalendarChoice(id: 'c1', name: 'Personal', primary: true),
    DeviceCalendarChoice(id: 'c2', name: 'Work', account: 'ada@work.example'),
  ];

  @override
  Future<List<String>> descriptionsBetween(String calendarId, DateTime from, DateTime to) async => [...existing];

  @override
  Future<void> create(String calendarId, CalendarEventDraft draft) async {
    created.add(draft);
    createdIn.add(calendarId);
    existing.add(draft.description);
  }

  @override
  Future<void> openSettings() async => openedSettings = true;
}
