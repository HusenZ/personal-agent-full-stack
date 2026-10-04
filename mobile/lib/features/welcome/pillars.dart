import 'package:flutter/material.dart';

/// Why someone would use this rather than a general chatbot. Each pillar is a
/// promise the backend actually keeps, so the copy must change if it stops.
class Pillar {
  const Pillar({required this.icon, required this.title, required this.body, required this.example});

  final IconData icon;
  final String title;
  final String body;

  /// A concrete moment where the difference shows.
  final String example;
}

const pillars = [
  Pillar(
    icon: Icons.psychology_outlined,
    title: 'It remembers you',
    body:
        'Tell it once. Your goals, routines and preferences carry into every conversation, '
        'and it gets more useful the more you talk.',
    example: '"Plan my week" already knows you train on Tuesdays and hate early starts.',
  ),
  Pillar(
    icon: Icons.verified_outlined,
    title: 'It shows its work',
    body:
        'Every reply is checked against what you have told it before you see it. '
        'When something cannot be backed up, it says so instead of making it up.',
    example: 'Each answer notes what it drew on: memories, your activity log, exact totals.',
  ),
  Pillar(
    icon: Icons.calculate_outlined,
    title: 'Facts, not guesses',
    body:
        'Numbers are counted from your records, never estimated by the model. '
        'Insights are labelled as fact, inference or suggestion.',
    example: '"How many hours did I study last week?" gets a sum, not a vibe.',
  ),
  Pillar(
    icon: Icons.shield_outlined,
    title: 'You stay in control',
    body:
        'See everything it remembers. Correct it, archive it or delete it, '
        'along with any chat or your whole account.',
    example: 'Nothing about you is hidden from you, and nothing is kept against your wishes.',
  ),
];

/// One pillar as a card: icon, title, explanation and an example.
class PillarCard extends StatelessWidget {
  const PillarCard({super.key, required this.pillar});

  final Pillar pillar;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(color: colors.primaryContainer, borderRadius: BorderRadius.circular(14)),
              child: Icon(pillar.icon, color: colors.onPrimaryContainer),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(pillar.title, style: theme.textTheme.titleMedium),
                  const SizedBox(height: 4),
                  Text(
                    pillar.body,
                    style: theme.textTheme.bodyMedium?.copyWith(color: colors.onSurfaceVariant, height: 1.4),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    pillar.example,
                    style: theme.textTheme.bodySmall?.copyWith(color: colors.primary, fontStyle: FontStyle.italic),
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

/// The pillars in a sheet, for "How it works" inside the app.
Future<void> showHowItWorks(BuildContext context) => showModalBottomSheet<void>(
  context: context,
  isScrollControlled: true,
  builder: (context) => DraggableScrollableSheet(
    expand: false,
    initialChildSize: 0.85,
    maxChildSize: 0.95,
    builder: (context, controller) => ListView(
      controller: controller,
      padding: const EdgeInsets.fromLTRB(20, 0, 20, 32),
      children: [
        Text('How it works', style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 4),
        Text(
          'What makes this different from a general chatbot.',
          style: Theme.of(context).textTheme.bodyMedium
              ?.copyWith(color: Theme.of(context).colorScheme.onSurfaceVariant),
        ),
        const SizedBox(height: 20),
        for (final pillar in pillars) ...[PillarCard(pillar: pillar), const SizedBox(height: 12)],
      ],
    ),
  ),
);
