import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:personal_intelligence/core/api/api_exception.dart';
import 'package:personal_intelligence/features/planning/plan_repository.dart';
import 'package:personal_intelligence/features/planning/week_plan_screen.dart';

String iso(DateTime day) => '${day.year}-${day.month.toString().padLeft(2, '0')}-${day.day.toString().padLeft(2, '0')}';

Map<String, dynamic> planJson({bool firstDone = false}) {
  final today = DateTime.now();
  final tomorrow = today.add(const Duration(days: 1));
  return {
    'id': 'p1',
    'starts_on': iso(today),
    'ends_on': iso(today.add(const Duration(days: 6))),
    'summary': 'Three runs around your evenings.',
    'blocks': [
      {
        'id': 'b1',
        'date': iso(today),
        'start': '19:00',
        'duration_minutes': 40,
        'title': 'Easy run',
        'category': 'health',
        'reason': 'Builds your base.',
        'evidence_ids': ['goal:1'],
        'done': firstDone,
      },
      {
        'id': 'b2',
        'date': iso(tomorrow),
        'start': null,
        'duration_minutes': 30,
        'title': 'Spanish flashcards',
        'category': 'something_new',
        'reason': 'Keeps the streak.',
        'evidence_ids': <String>[],
        'done': false,
      },
    ],
    'evidence': [
      {'id': 'goal:1', 'kind': 'goal', 'content': 'Run a half marathon'},
    ],
  };
}

void main() {
  group('reading a plan', () {
    test('dates are calendar dates and unknown kinds are tolerated', () {
      final plan = WeekPlan.fromJson(planJson());

      expect(plan.days, hasLength(7));
      expect(plan.on(DateTime.now()).single.title, 'Easy run');
      expect(plan.blocks.last.category, BlockCategory.other);
      expect(plan.evidence['goal:1'], 'Run a half marathon');
      expect(parseDate('2026-10-03'), DateTime(2026, 10, 3));
    });

    test('times and durations read naturally', () {
      expect(durationLabel(40), '40 min');
      expect(durationLabel(90), '1 h 30 min');
      expect(durationLabel(120), '2 h');
      expect(endTime('18:30', 40), '19:10');
      expect(rangeLabel(DateTime(2026, 10, 3), DateTime(2026, 10, 9)), '3 – 9 Oct');
      expect(rangeLabel(DateTime(2026, 9, 28), DateTime(2026, 10, 4)), '28 Sep – 4 Oct');
    });
  });

  group('the week screen', () {
    Future<_Plans> open(WidgetTester tester, _Plans plans) async {
      tester.view.physicalSize = const Size(1080, 2400);
      tester.view.devicePixelRatio = 2.75;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [planRepositoryProvider.overrideWithValue(plans)],
          retry: (_, _) => null,
          child: const MaterialApp(home: WeekPlanScreen()),
        ),
      );
      await tester.pumpAndSettle();
      return plans;
    }

    testWidgets('a first plan is made on request and shown on today', (tester) async {
      final plans = await open(tester, _Plans());

      await tester.tap(find.byKey(const Key('generate-plan')));
      await tester.pump();
      expect(find.byKey(const Key('plan-generating')), findsOneWidget);
      plans.release();
      await tester.pumpAndSettle();

      expect(find.text('Three runs around your evenings.'), findsOneWidget);
      expect(find.text('Easy run'), findsOneWidget);
      expect(find.text('Builds your base.'), findsNothing, reason: 'why stays folded until asked');
      await tester.tap(find.byKey(const Key('block-b1')));
      await tester.pumpAndSettle();
      expect(find.text('Builds your base.'), findsOneWidget);
      expect(find.text('Run a half marathon'), findsOneWidget, reason: 'what the block serves');
      expect(find.text('Spanish flashcards'), findsNothing, reason: 'that is tomorrow');
    });

    testWidgets('choosing a day shows that day', (tester) async {
      await open(tester, _Plans(existing: WeekPlan.fromJson(planJson())));

      final tomorrow = DateTime.now().add(const Duration(days: 1));
      await tester.tap(find.byKey(Key('day-${tomorrow.day}')));
      await tester.pumpAndSettle();

      expect(find.text('Spanish flashcards'), findsOneWidget);
      expect(find.text('Any time · 30 min'), findsOneWidget);
    });

    testWidgets('ticking a block is saved and counted', (tester) async {
      final plans = await open(tester, _Plans(existing: WeekPlan.fromJson(planJson())));

      await tester.tap(find.byKey(const Key('done-b1')));
      await tester.pumpAndSettle();

      expect(plans.ticked, ['b1:true']);
      expect(find.text('1 of 2 done'), findsOneWidget);
    });

    testWidgets('a tick the server refuses is undone', (tester) async {
      await open(tester, _Plans(existing: WeekPlan.fromJson(planJson()), failTicks: true));

      await tester.tap(find.byKey(const Key('done-b1')));
      await tester.pumpAndSettle();

      expect(find.text('0 of 2 done'), findsOneWidget);
    });

    testWidgets('with nothing known, it asks for goals instead of failing', (tester) async {
      final plans = await open(
        tester,
        _Plans(
          failure: const ApiException(statusCode: 409, code: 'INSUFFICIENT_CONTEXT', message: 'x'),
        ),
      );

      await tester.tap(find.byKey(const Key('generate-plan')));
      plans.release();
      await tester.pumpAndSettle();

      expect(find.text('I need to know your goals first.'), findsOneWidget);
      expect(find.byKey(const Key('generate-plan')), findsOneWidget);
    });
  });
}

class _Plans implements PlanRepository {
  _Plans({this.existing, this.failure, this.failTicks = false});

  final WeekPlan? existing;
  final ApiException? failure;
  final bool failTicks;
  final ticked = <String>[];
  final _gate = Completer<void>();

  /// Lets a pending [generate] finish, so the screen's loading state can be
  /// seen first.
  void release() {
    if (!_gate.isCompleted) {
      _gate.complete();
    }
  }

  @override
  Future<WeekPlan?> latest() async => existing;

  @override
  Future<WeekPlan> generate() async {
    await _gate.future;
    if (failure != null) {
      throw failure!;
    }
    return WeekPlan.fromJson(planJson());
  }

  @override
  Future<WeekPlan> setDone(String planId, String blockId, {required bool done}) async {
    if (failTicks) {
      throw const ApiException(statusCode: 500, code: 'INTERNAL_ERROR', message: 'x');
    }
    ticked.add('$blockId:$done');
    return WeekPlan.fromJson(planJson(firstDone: done));
  }
}
