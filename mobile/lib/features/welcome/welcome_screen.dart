import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/ui/components.dart';
import 'pillars.dart';

/// The first thing a signed-out person sees: what the app is for and why it
/// is worth an account, before being asked for one.
class WelcomeScreen extends StatefulWidget {
  const WelcomeScreen({super.key});

  @override
  State<WelcomeScreen> createState() => _WelcomeScreenState();
}

class _WelcomeScreenState extends State<WelcomeScreen> {
  final _pages = PageController();
  int _page = 0;

  // The introduction, then one page per pillar.
  static final _count = pillars.length + 1;

  @override
  void dispose() {
    _pages.dispose();
    super.dispose();
  }

  void _next() {
    if (_page == _count - 1) {
      context.go('/sign-in?mode=register');
      return;
    }
    _pages.nextPage(duration: const Duration(milliseconds: 350), curve: Curves.easeOutCubic);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final last = _page == _count - 1;

    return Scaffold(
      body: DecoratedBox(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [colors.primaryContainer.withValues(alpha: 0.7), colors.surface],
            stops: const [0, 0.6],
          ),
        ),
        child: SafeArea(
          child: Column(
            children: [
              Align(
                alignment: Alignment.centerRight,
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(8, 4, 8, 0),
                  child: TextButton(
                    key: const Key('welcome-sign-in'),
                    onPressed: () => context.go('/sign-in'),
                    child: const Text('I have an account'),
                  ),
                ),
              ),
              Expanded(
                child: PageView(
                  controller: _pages,
                  onPageChanged: (page) => setState(() => _page = page),
                  children: [
                    const _Intro(),
                    for (final pillar in pillars) _PillarPage(pillar: pillar),
                  ],
                ),
              ),
              _Dots(count: _count, current: _page),
              Padding(
                padding: const EdgeInsets.fromLTRB(24, 20, 24, 16),
                child: SizedBox(
                  width: double.infinity,
                  child: FilledButton(
                    key: const Key('welcome-next'),
                    onPressed: _next,
                    child: Text(last ? 'Create your account' : (_page == 0 ? 'See how it works' : 'Next')),
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

/// Scrolls when the screen is short, and centres its content when it is not.
class _Page extends StatelessWidget {
  const _Page({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, constraints) => SingleChildScrollView(
      padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 24),
      child: ConstrainedBox(
        constraints: BoxConstraints(minHeight: constraints.maxHeight - 48),
        child: Column(mainAxisAlignment: MainAxisAlignment.center, children: children),
      ),
    ),
  );
}

class _Intro extends StatelessWidget {
  const _Intro();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return _Page(
      children: [
        const BrandMark(size: 96),
        const SizedBox(height: 36),
        Text(
          'An assistant that\nactually knows you',
          style: theme.textTheme.headlineMedium?.copyWith(height: 1.15),
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 16),
        Text(
          'General chatbots start from zero every time. This one builds an understanding of your goals, '
          'habits and plans, and every answer is grounded in it.',
          style: theme.textTheme.bodyLarge?.copyWith(color: theme.colorScheme.onSurfaceVariant, height: 1.45),
          textAlign: TextAlign.center,
        ),
      ],
    );
  }
}

class _PillarPage extends StatelessWidget {
  const _PillarPage({required this.pillar});

  final Pillar pillar;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return _Page(
      children: [
        ExcludeSemantics(
          child: Container(
            width: 132,
            height: 132,
            decoration: BoxDecoration(shape: BoxShape.circle, color: colors.primary.withValues(alpha: 0.08)),
            alignment: Alignment.center,
            child: Container(
              width: 92,
              height: 92,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [colors.primary, colors.tertiary],
                ),
              ),
              child: Icon(pillar.icon, size: 44, color: colors.onPrimary),
            ),
          ),
        ),
        const SizedBox(height: 36),
        Text(pillar.title, style: theme.textTheme.headlineMedium, textAlign: TextAlign.center),
        const SizedBox(height: 14),
        Text(
          pillar.body,
          style: theme.textTheme.bodyLarge?.copyWith(color: colors.onSurfaceVariant, height: 1.45),
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 24),
        Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(color: colors.surfaceContainerHigh, borderRadius: BorderRadius.circular(16)),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(Icons.format_quote_rounded, color: colors.primary),
              const SizedBox(width: 10),
              Expanded(child: Text(pillar.example, style: theme.textTheme.bodyMedium?.copyWith(height: 1.4))),
            ],
          ),
        ),
      ],
    );
  }
}

class _Dots extends StatelessWidget {
  const _Dots({required this.count, required this.current});

  final int count;
  final int current;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Semantics(
      label: 'Page ${current + 1} of $count',
      child: ExcludeSemantics(
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            for (var index = 0; index < count; index++)
              AnimatedContainer(
                duration: const Duration(milliseconds: 250),
                margin: const EdgeInsets.symmetric(horizontal: 4),
                width: index == current ? 24 : 8,
                height: 8,
                decoration: BoxDecoration(
                  color: index == current ? colors.primary : colors.outlineVariant,
                  borderRadius: BorderRadius.circular(4),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
