import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_client.dart';
import '../../core/api/api_exception.dart';
import '../../core/providers.dart';

/// What a block is for. Unknown values from a newer server read as [other].
enum BlockCategory { goal, habit, health, learning, admin, social, rest, other }

class PlanBlock {
  const PlanBlock({
    required this.id,
    required this.date,
    required this.durationMinutes,
    required this.title,
    required this.category,
    required this.reason,
    required this.evidenceIds,
    required this.done,
    this.start,
    this.startsAt,
    this.endsAt,
  });

  factory PlanBlock.fromJson(Map<String, dynamic> json) => PlanBlock(
    id: json['id'] as String,
    date: parseDate(json['date'] as String),
    start: json['start'] as String?,
    startsAt: _instant(json['starts_at']),
    endsAt: _instant(json['ends_at']),
    durationMinutes: json['duration_minutes'] as int,
    title: json['title'] as String,
    category: BlockCategory.values.asNameMap()[json['category']] ?? BlockCategory.other,
    reason: json['reason'] as String? ?? '',
    evidenceIds: [for (final id in json['evidence_ids'] as List<dynamic>? ?? const []) id as String],
    done: json['done'] as bool? ?? false,
  );

  final String id;

  /// A calendar date in the plan's time zone, at local midnight.
  final DateTime date;

  /// "HH:MM", or null when the block can happen any time that day.
  final String? start;

  /// [start] as exact instants, resolved by the server in the plan's time
  /// zone; null for an any-time block.
  final DateTime? startsAt;
  final DateTime? endsAt;
  final int durationMinutes;
  final String title;
  final BlockCategory category;
  final String reason;
  final List<String> evidenceIds;
  final bool done;

  PlanBlock withDone(bool value) => PlanBlock(
    id: id,
    date: date,
    start: start,
    startsAt: startsAt,
    endsAt: endsAt,
    durationMinutes: durationMinutes,
    title: title,
    category: category,
    reason: reason,
    evidenceIds: evidenceIds,
    done: value,
  );
}

class WeekPlan {
  const WeekPlan({
    required this.id,
    required this.startsOn,
    required this.endsOn,
    required this.summary,
    required this.blocks,
    required this.evidence,
    this.timezone = 'UTC',
  });

  factory WeekPlan.fromJson(Map<String, dynamic> json) => WeekPlan(
    id: json['id'] as String,
    startsOn: parseDate(json['starts_on'] as String),
    endsOn: parseDate(json['ends_on'] as String),
    summary: json['summary'] as String,
    timezone: json['timezone'] as String? ?? 'UTC',
    blocks: [for (final block in json['blocks'] as List<dynamic>) PlanBlock.fromJson(block as Map<String, dynamic>)],
    evidence: {
      for (final item in json['evidence'] as List<dynamic>)
        (item as Map<String, dynamic>)['id'] as String: item['content'] as String,
    },
  );

  final String id;
  final DateTime startsOn;
  final DateTime endsOn;
  final String summary;

  /// The IANA zone the plan's times are in, e.g. "Asia/Kolkata".
  final String timezone;
  final List<PlanBlock> blocks;

  /// Evidence id → what the user said, to show what a block serves.
  final Map<String, String> evidence;

  List<DateTime> get days => [for (var offset = 0; offset < 7; offset++) startsOn.add(Duration(days: offset))];

  List<PlanBlock> on(DateTime day) => [
    for (final block in blocks)
      if (sameDate(block.date, day)) block,
  ];

  int get doneCount => blocks.where((block) => block.done).length;

  WeekPlan withBlock(PlanBlock updated) => WeekPlan(
    id: id,
    startsOn: startsOn,
    endsOn: endsOn,
    summary: summary,
    timezone: timezone,
    blocks: [for (final block in blocks) block.id == updated.id ? updated : block],
    evidence: evidence,
  );
}

DateTime? _instant(Object? value) => value == null ? null : DateTime.parse(value as String);

/// "2026-10-03" as a local date, never shifted by a UTC conversion.
DateTime parseDate(String value) {
  final parts = value.split('-').map(int.parse).toList();
  return DateTime(parts[0], parts[1], parts[2]);
}

bool sameDate(DateTime a, DateTime b) => a.year == b.year && a.month == b.month && a.day == b.day;

class PlanRepository {
  PlanRepository(this._api);

  final ApiClient _api;

  /// The newest plan, or null if none has been made.
  Future<WeekPlan?> latest() async {
    try {
      return WeekPlan.fromJson((await _api.get('/api/v1/plans/week/latest')).object);
    } on ApiException catch (error) {
      if (error.code == 'PLAN_NOT_FOUND') {
        return null;
      }
      rethrow;
    }
  }

  Future<WeekPlan> generate() async => WeekPlan.fromJson((await _api.post('/api/v1/plans/week')).object);

  Future<WeekPlan> setDone(String planId, String blockId, {required bool done}) async =>
      WeekPlan.fromJson((await _api.patch('/api/v1/plans/$planId/blocks/$blockId', body: {'done': done})).object);
}

final planRepositoryProvider = Provider<PlanRepository>((ref) => PlanRepository(ref.watch(apiClientProvider)));

/// The current plan, and the user's actions on it.
class WeekPlanController extends AsyncNotifier<WeekPlan?> {
  @override
  Future<WeekPlan?> build() => ref.read(planRepositoryProvider).latest();

  /// Makes a new plan. Errors are thrown to the caller to show, and the plan
  /// already on screen stays.
  Future<void> generate() async {
    final plan = await ref.read(planRepositoryProvider).generate();
    state = AsyncData(plan);
  }

  /// Ticks a block at once and confirms with the server; a failure puts it
  /// back and is thrown to the caller.
  Future<void> toggle(PlanBlock block) async {
    final plan = state.value;
    if (plan == null) {
      return;
    }
    state = AsyncData(plan.withBlock(block.withDone(!block.done)));
    try {
      final saved = await ref.read(planRepositoryProvider).setDone(plan.id, block.id, done: !block.done);
      state = AsyncData(saved);
    } on Object {
      state = AsyncData(plan);
      rethrow;
    }
  }
}

final weekPlanProvider = AsyncNotifierProvider<WeekPlanController, WeekPlan?>(WeekPlanController.new);
