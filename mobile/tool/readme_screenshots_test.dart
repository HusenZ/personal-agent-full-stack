// Renders the README screenshots from the app's real screens, fed with sample
// data, so no backend, device or model is needed:
//
//   cd mobile && flutter test tool/readme_screenshots_test.dart
//
// The PNGs land in docs/screenshots/ at the repository root. This lives outside
// test/ so a plain `flutter test` never runs it: these are pictures, not
// assertions, and text rendering differs slightly between machines.

import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart' show Override;
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:personal_intelligence/app/theme.dart';
import 'package:personal_intelligence/core/api/api_client.dart';
import 'package:personal_intelligence/core/api/sse.dart';
import 'package:personal_intelligence/core/auth/token_store.dart';
import 'package:personal_intelligence/core/providers.dart';
import 'package:personal_intelligence/features/analysis/analysis_screen.dart';
import 'package:personal_intelligence/features/chat/chat_repository.dart';
import 'package:personal_intelligence/features/chat/chat_screen.dart';
import 'package:personal_intelligence/features/home/home_shell.dart';
import 'package:personal_intelligence/features/home/today_screen.dart';
import 'package:personal_intelligence/features/memories/memories_repository.dart';
import 'package:personal_intelligence/features/memories/memories_screen.dart';
import 'package:personal_intelligence/features/onboarding/onboarding_repository.dart';
import 'package:personal_intelligence/features/onboarding/onboarding_screen.dart';
import 'package:personal_intelligence/features/planning/plan_repository.dart';
import 'package:personal_intelligence/features/planning/week_plan_screen.dart';
import 'package:personal_intelligence/features/profile/profile_repository.dart';
import 'package:personal_intelligence/features/welcome/welcome_screen.dart';

/// A modern phone's screen, in logical pixels, and the density it is drawn at.
const _screenWidth = 402.0;
const _screenHeight = 874.0;
const _screen = Size(_screenWidth, _screenHeight);
const _ratio = 2.0;
const _statusBar = 40.0;
const _gestureBar = 20.0;

/// The phone body around the screen, and the transparent margin around that
/// which leaves room for its shadow.
const _bezel = 11.0;
const _margin = 28.0;
const _framed = Size(_screenWidth + 2 * (_bezel + _margin), _screenHeight + 2 * (_bezel + _margin));

final _output = Directory('../docs/screenshots');

void main() {
  setUpAll(_loadFonts);

  testWidgets('today', (tester) async {
    await _shoot(tester, 'today', _app(router: _shell('/today')));
  });

  testWidgets('chat', (tester) async {
    await _shoot(
      tester,
      'chat',
      _app(home: const ChatScreen()),
      prepare: () async {
        for (final question in _script.keys) {
          await tester.enterText(find.byKey(const Key('message-input')), question);
          await tester.pump();
          await tester.tap(find.byKey(const Key('send')));
          await tester.pumpAndSettle();
        }
      },
    );
  });

  testWidgets('receipt', (tester) async {
    await _shoot(
      tester,
      'receipt',
      _app(home: const ChatScreen()),
      prepare: () async {
        for (final question in _script.keys) {
          await tester.enterText(find.byKey(const Key('message-input')), question);
          await tester.pump();
          await tester.tap(find.byKey(const Key('send')));
          await tester.pumpAndSettle();
        }
        await tester.tap(find.byKey(const Key('answer-receipt')).last);
        await tester.pumpAndSettle();
      },
    );
  });

  testWidgets('week plan', (tester) async {
    await _shoot(tester, 'week_plan', _app(home: const WeekPlanScreen()));
  });

  testWidgets('memory', (tester) async {
    await _shoot(tester, 'memory', _app(router: _shell('/memories')));
  });

  testWidgets('analysis', (tester) async {
    await _shoot(tester, 'analysis', _app(home: const AnalysisScreen()));
  });

  testWidgets('onboarding', (tester) async {
    await _shoot(tester, 'onboarding', _app(home: const OnboardingScreen()));
  });

  testWidgets('welcome', (tester) async {
    await _shoot(tester, 'welcome', _app(home: const WelcomeScreen()));
  });
}

// Rendering ------------------------------------------------------------------

/// Draws [app] on a phone-sized view, then draws that picture inside a phone
/// frame, and writes the result as `<name>.png`.
Future<void> _shoot(WidgetTester tester, String name, Widget app, {Future<void> Function()? prepare}) async {
  addTearDown(tester.view.reset);
  tester.view
    ..physicalSize = _screen * _ratio
    ..devicePixelRatio = _ratio
    ..padding = const FakeViewPadding(top: _statusBar * _ratio, bottom: _gestureBar * _ratio)
    ..viewPadding = const FakeViewPadding(top: _statusBar * _ratio, bottom: _gestureBar * _ratio);

  final screenKey = GlobalKey();
  await tester.pumpWidget(RepaintBoundary(key: screenKey, child: app));
  await tester.pumpAndSettle();
  if (prepare != null) {
    await prepare();
  }
  final screen = await _capture(tester, screenKey);

  tester.view
    ..physicalSize = _framed * _ratio
    ..resetPadding()
    ..resetViewPadding();
  final frameKey = GlobalKey();
  await tester.pumpWidget(RepaintBoundary(key: frameKey, child: _Phone(screen)));
  await tester.pump();
  final framed = await _capture(tester, frameKey);

  final png = await tester.runAsync(() => framed.toByteData(format: ui.ImageByteFormat.png));
  _output.createSync(recursive: true);
  File('${_output.path}/$name.png').writeAsBytesSync(png!.buffer.asUint8List());
}

Future<ui.Image> _capture(WidgetTester tester, GlobalKey key) async {
  final boundary = key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
  return (await tester.runAsync(() => boundary.toImage(pixelRatio: _ratio)))!;
}

/// Tests draw text with a placeholder font unless real ones are loaded. The
/// Flutter SDK ships Roboto and the Material icons, which is what the app uses
/// on Android.
Future<void> _loadFonts() async {
  final executable = Platform.resolvedExecutable;
  final sdk = Platform.environment['FLUTTER_ROOT'] ?? executable.substring(0, executable.indexOf('/bin/cache/'));
  final fonts = '$sdk/bin/cache/artifacts/material_fonts';

  Future<void> load(String family, List<String> files) async {
    final loader = FontLoader(family);
    for (final file in files) {
      loader.addFont(Future.value(ByteData.sublistView(File('$fonts/$file').readAsBytesSync())));
    }
    await loader.load();
  }

  await load('Roboto', [
    'Roboto-Light.ttf',
    'Roboto-Regular.ttf',
    'Roboto-Italic.ttf',
    'Roboto-Medium.ttf',
    'Roboto-Bold.ttf',
    'Roboto-Black.ttf',
  ]);
  await load('MaterialIcons', ['MaterialIcons-Regular.otf']);
}

/// A plain Android-style phone: dark body, status bar, camera, gesture bar.
class _Phone extends StatelessWidget {
  const _Phone(this.screen);

  final ui.Image screen;

  @override
  Widget build(BuildContext context) {
    const ink = Color(0xFF1C1B1F);
    const status = TextStyle(fontFamily: 'Roboto', fontSize: 14, fontWeight: FontWeight.w600, color: ink);
    // The render time, so the clock agrees with message times and greetings.
    final clock = '${_now.hour.toString().padLeft(2, '0')}:${_now.minute.toString().padLeft(2, '0')}';

    return Directionality(
      textDirection: TextDirection.ltr,
      child: Center(
        child: Container(
          width: _screen.width + 2 * _bezel,
          height: _screen.height + 2 * _bezel,
          padding: const EdgeInsets.all(_bezel),
          decoration: const BoxDecoration(
            color: Color(0xFF16161A),
            borderRadius: BorderRadius.all(Radius.circular(54)),
            boxShadow: [BoxShadow(color: Color(0x40000000), blurRadius: 22, offset: Offset(0, 10))],
          ),
          child: ClipRRect(
            borderRadius: const BorderRadius.all(Radius.circular(44)),
            child: Stack(
              children: [
                RawImage(image: screen, scale: _ratio, width: _screen.width, height: _screen.height),
                Positioned(
                  top: 0,
                  left: 30,
                  right: 26,
                  height: _statusBar,
                  child: Row(
                    children: [
                      Text(clock, style: status),
                      Spacer(),
                      Icon(Icons.signal_cellular_alt, size: 16, color: ink),
                      SizedBox(width: 4),
                      Icon(Icons.wifi, size: 16, color: ink),
                      SizedBox(width: 4),
                      Icon(Icons.battery_full, size: 16, color: ink),
                    ],
                  ),
                ),
                const Positioned(
                  top: 14,
                  left: 0,
                  right: 0,
                  child: Center(
                    child: SizedBox.square(
                      dimension: 12,
                      child: DecoratedBox(
                        decoration: BoxDecoration(color: Colors.black, shape: BoxShape.circle),
                      ),
                    ),
                  ),
                ),
                Positioned(
                  bottom: 7,
                  left: 0,
                  right: 0,
                  child: Center(
                    child: Container(
                      width: 108,
                      height: 4,
                      decoration: BoxDecoration(
                        color: ink.withValues(alpha: 0.55),
                        borderRadius: const BorderRadius.all(Radius.circular(2)),
                      ),
                    ),
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

// The app, wired to sample data -----------------------------------------------

Widget _app({Widget? home, GoRouter? router}) {
  final theme = AppTheme.light();
  return ProviderScope(
    overrides: _overrides(),
    retry: (_, _) => null,
    child: router == null
        ? MaterialApp(debugShowCheckedModeBanner: false, theme: theme, home: home)
        : MaterialApp.router(debugShowCheckedModeBanner: false, theme: theme, routerConfig: router),
  );
}

/// The signed-in layout with its bottom navigation, as the app builds it.
GoRouter _shell(String location) => GoRouter(
  initialLocation: location,
  routes: [
    StatefulShellRoute.indexedStack(
      builder: (_, _, shell) => HomeShell(shell: shell),
      branches: [
        StatefulShellBranch(
          routes: [GoRoute(path: '/today', builder: (_, _) => const TodayScreen())],
        ),
        StatefulShellBranch(
          routes: [GoRoute(path: '/chats', builder: (_, _) => const SizedBox())],
        ),
        StatefulShellBranch(
          routes: [GoRoute(path: '/memories', builder: (_, _) => const MemoriesScreen())],
        ),
        StatefulShellBranch(
          routes: [GoRoute(path: '/you', builder: (_, _) => const SizedBox())],
        ),
      ],
    ),
  ],
);

List<Override> _overrides() => [
  // Screens that only call the API when tapped still read a client to build.
  apiClientProvider.overrideWithValue(
    ApiClient(
      baseUrl: Uri.parse('https://api.invalid'),
      httpClient: MockClient((_) async => http.Response('', 503)),
      tokens: MemoryTokenStore(),
    ),
  ),
  profileProvider.overrideWith((_) async => _profile),
  analysisProvider.overrideWith((_) async => _analysis()),
  onboardingStatusProvider.overrideWith(
    (_) async => const OnboardingTurn(isComplete: true, questionsAsked: 9, overallConfidence: 0.86),
  ),
  onboardingRepositoryProvider.overrideWithValue(_Interview()),
  recentMemoriesProvider.overrideWith(
    (_) async => PageOf(items: _memories().take(3).toList(), page: 1, totalPages: 1, hasNext: false, totalItems: 37),
  ),
  memoriesProvider.overrideWith((_) async => _memories()),
  chatRepositoryProvider.overrideWithValue(_Chats()),
  planRepositoryProvider.overrideWithValue(_Plans()),
];

// Sample data: one fictional person, consistent across every screen ----------

final _now = DateTime.now();

DateTime _ago({int days = 0, int hours = 0}) => _now.subtract(Duration(days: days, hours: hours));

String _date(int daysFromToday) {
  final day = _now.add(Duration(days: daysFromToday));
  return '${day.year}-${day.month.toString().padLeft(2, '0')}-${day.day.toString().padLeft(2, '0')}';
}

const _profile = Profile(
  timezone: 'Asia/Kolkata',
  goals: [
    Goal('Run a half marathon in March', GoalPriority.high),
    Goal('Ship the beta of my budgeting app', GoalPriority.high),
    Goal('Read 12 books this year', GoalPriority.low),
  ],
  constraints: ['Works 9:30 to 6:30 on weekdays', 'No runs on back-to-back days (knee)'],
  preferences: {'study_time': 'Mornings, before work'},
);

List<Memory> _memories() {
  Memory memory(String id, String content, String type, {int days = 0, int hours = 0}) => Memory(
    id: id,
    content: content,
    type: type,
    status: MemoryStatus.active,
    isSensitive: false,
    createdAt: _ago(days: days, hours: hours),
  );

  return [
    memory('m1', 'Signed up for the Pune half marathon on 9 March', 'goal', hours: 2),
    memory('m2', 'Knee aches after running two days in a row', 'constraint', hours: 5),
    memory('m3', 'Prefers to study in the mornings, before work', 'preference', days: 1),
    memory('m4', 'Studied system design for 2 hours on Thursday', 'event', days: 2),
    memory('m5', 'Is building a budgeting app in Flutter, aiming for a beta in December', 'fact', days: 3),
    memory('m6', 'Finished "Atomic Habits" and wants to read more non-fiction', 'experience', days: 4),
    memory('m7', 'Sleeps better on days that start with a run', 'observation', days: 5),
    memory('m8', 'Does a long run every Saturday morning', 'plan', days: 6),
    memory('m9', 'Travelling to Mumbai next weekend', 'temporary_context', days: 6),
    memory('m10', 'Moved to Pune in October', 'fact', days: 9),
  ];
}

Analysis _analysis() => Analysis(
  createdAt: _ago(days: 1),
  degraded: false,
  sections: const {
    'current_situation': [
      Claim('fact', 'Works full time as a backend developer, 9:30 to 6:30 on weekdays', 0.95),
      Claim('observation', 'Has run three times a week for the past month', 0.8),
    ],
    'goals': [
      Claim('fact', 'Half marathon in Pune on 9 March', 0.95),
      Claim('fact', 'Budgeting app in beta before the new year', 0.9),
    ],
    'strengths': [Claim('observation', 'Keeps a morning routine once it is set', 0.75)],
    'constraints': [Claim('fact', 'Knee aches after back-to-back run days', 0.9)],
    'bottlenecks': [
      Claim('inference', 'Evenings do double duty: the app and recovery compete for the same hours', 0.65),
      Claim('uncertainty', 'Not yet clear how much sleep you get on workdays', 0.4),
    ],
    'priorities': [Claim('recommendation', 'Keep three easy runs a week, never on consecutive days', 0.8)],
    'suggested_actions': [
      Claim('recommendation', 'Give the app two fixed evenings a week', 0.7),
      Claim('recommendation', 'Add a 30-minute mobility session after each long run', 0.7),
    ],
  },
);

Map<String, dynamic> _plan() {
  Map<String, Object?> block(
    String id,
    int day,
    String? start,
    int minutes,
    String title,
    String category,
    String reason,
    List<String> evidence, {
    bool done = false,
  }) => {
    'id': id,
    'date': _date(day),
    'start': start,
    'duration_minutes': minutes,
    'title': title,
    'category': category,
    'reason': reason,
    'evidence_ids': evidence,
    'done': done,
  };

  return {
    'id': 'p1',
    'starts_on': _date(0),
    'ends_on': _date(6),
    'summary': 'Three runs on non-consecutive days and two fixed evenings for the beta.',
    'blocks': [
      block(
        'b1',
        0,
        '07:00',
        40,
        'Easy run, 6 km',
        'health',
        'Tomorrow is a rest day for your knee, so today can be a run.',
        ['goal:run', 'constraint:knee'],
        done: true,
      ),
      block('b2', 0, '08:15', 45, 'System design reading', 'learning', 'You study best in the mornings, before work.', [
        'preference:study_time',
      ], done: true),
      block(
        'b3',
        0,
        '19:30',
        90,
        'Beta: budget categories screen',
        'goal',
        'One of your two fixed evenings for the app.',
        ['goal:beta'],
      ),
      block('b4', 0, '22:00', 30, 'Read 30 pages', 'habit', 'Thirty pages a night keeps you on pace for 12 books.', [
        'goal:books',
      ]),
      block('b5', 1, '07:30', 30, 'Mobility and stretching', 'health', 'A day off running, as your knee needs.', [
        'constraint:knee',
      ]),
      block('b6', 1, null, 20, 'Renew gym membership', 'admin', 'It runs out this week.', []),
      block('b7', 2, '06:45', 45, 'Tempo run', 'health', 'One harder run a week builds race pace.', ['goal:run']),
      block('b8', 2, '19:30', 90, 'Beta: fix sync bugs', 'goal', 'Your second evening for the app.', ['goal:beta']),
      block('b9', 3, '07:30', 30, 'Mobility', 'health', 'Recovery before the weekend.', ['constraint:knee']),
      block('b10', 4, '07:00', 40, 'Easy run', 'health', 'Keeps three runs this week.', ['goal:run']),
      block('b11', 5, '07:00', 90, 'Long run, 14 km', 'health', 'Your Saturday long run.', ['goal:run']),
      block('b12', 5, '19:00', 120, 'Dinner with friends', 'social', 'Nothing else planned that evening.', []),
      block('b13', 6, '10:00', 20, 'Weekly review', 'admin', 'A look back before planning again.', []),
      block('b14', 6, '16:00', 60, 'Rest and recover', 'rest', 'The day after a long run.', ['goal:run']),
    ],
    'evidence': [
      {'id': 'goal:run', 'kind': 'goal', 'content': 'Run a half marathon in March'},
      {'id': 'goal:beta', 'kind': 'goal', 'content': 'Ship the beta of my budgeting app'},
      {'id': 'goal:books', 'kind': 'goal', 'content': 'Read 12 books this year'},
      {'id': 'constraint:knee', 'kind': 'constraint', 'content': 'No runs on back-to-back days (knee)'},
      {'id': 'preference:study_time', 'kind': 'preference', 'content': 'Mornings, before work'},
    ],
  };
}

/// Two chat turns, each with the progress events the backend streams for it.
final _script = <String, List<TurnEvent>>{
  'How much did I study last week?': [
    _progress('query_understood', {
      'needs': ['totals'],
    }),
    _progress('retrieval_completed', {'memories': 0, 'timeline_entries': 4, 'has_totals': true}),
    _progress('draft_completed', {'attempt': 1, 'failed': false}),
    _progress('verification_completed', {'grounded': true}),
    _completed(
      '1',
      'How much did I study last week?',
      'You logged 6 h 30 min of study last week, across four sessions. '
          "The longest was Thursday's 2-hour system design block.",
    ),
  ],
  'Should I run tomorrow morning?': [
    _progress('query_understood', {
      'needs': ['semantic'],
    }),
    _progress('retrieval_completed', {'memories': 5, 'timeline_entries': 0, 'has_totals': false}),
    _progress('evidence_graded', {'memories_kept': 3, 'sufficient': true}),
    _progress('draft_completed', {'attempt': 1, 'failed': false}),
    _progress('verification_completed', {'grounded': true}),
    _completed(
      '2',
      'Should I run tomorrow morning?',
      "I'd skip it. You ran this morning, and you've told me your knee aches after two run days in a row. "
          'Tomorrow has a 30-minute mobility session instead, and your next run is the tempo run the day after.',
    ),
  ],
};

TurnProgress _progress(String event, Map<String, Object?> data) =>
    describeEvent(SseEvent(event, jsonEncode(data)))! as TurnProgress;

TurnCompleted _completed(String id, String question, String answer) {
  ChatMessage message(String role, String content) => ChatMessage.fromJson({
    'id': '$role-$id',
    'role': role,
    'content': content,
    'created_at': _now.toUtc().toIso8601String(),
  });

  return TurnCompleted(
    conversationId: 'c1',
    title: 'Training and study',
    userMessage: message('user', question),
    reply: message('assistant', answer),
    degraded: false,
    grounded: true,
  );
}

class _Chats implements ChatRepository {
  @override
  Future<PageOf<ConversationSummary>> conversations({int page = 1}) async => PageOf(
    items: [
      ConversationSummary(id: 'c1', title: 'Training and study', messageCount: 4, lastMessageAt: _ago(hours: 1)),
      ConversationSummary(id: 'c2', title: 'Beta launch checklist', messageCount: 8, lastMessageAt: _ago(days: 2)),
      ConversationSummary(id: 'c3', title: 'Books for the new year', messageCount: 6, lastMessageAt: _ago(days: 5)),
    ],
    page: 1,
    totalPages: 1,
    hasNext: false,
    totalItems: 3,
  );

  @override
  Stream<TurnEvent> send(String message, {String? conversationId}) => Stream.fromIterable(_script[message]!);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _Plans implements PlanRepository {
  @override
  Future<WeekPlan?> latest() async => WeekPlan.fromJson(_plan());

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _Interview implements OnboardingRepository {
  @override
  Future<OnboardingTurn> start() async => const OnboardingTurn(
    isComplete: false,
    questionsAsked: 4,
    overallConfidence: 0.55,
    question:
        'You mentioned training for a half marathon. Which days can you usually run, '
        'and is there anything that limits how often?',
  );

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
