import 'package:flutter/material.dart';

import 'chat_repository.dart';

/// The short line under a reply: what it was checked against.
List<String> receiptSummary(AnswerReceipt receipt) => [
  if (receipt.grounded == true) 'Checked',
  if (receipt.memories > 0) receipt.memories == 1 ? '1 memory' : '${receipt.memories} memories',
  if (receipt.timelineEntries > 0) 'your timeline',
  if (receipt.usedTotals) 'exact totals',
  if (receipt.memories == 0 && receipt.timelineEntries == 0 && !receipt.usedTotals) 'general knowledge',
];

/// A tappable line under a reply that opens [showReceipt].
class ReceiptLine extends StatelessWidget {
  const ReceiptLine({super.key, required this.receipt});

  final AnswerReceipt receipt;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final checked = receipt.grounded == true;
    final tone = checked ? Colors.green.shade600 : colors.onSurfaceVariant;
    return InkWell(
      key: const Key('answer-receipt'),
      borderRadius: BorderRadius.circular(8),
      onTap: () => showReceipt(context, receipt),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(checked ? Icons.verified_outlined : Icons.info_outline, size: 14, color: tone),
            const SizedBox(width: 4),
            Flexible(
              child: Text(
                receiptSummary(receipt).join(' · '),
                style: theme.textTheme.labelSmall?.copyWith(color: tone),
              ),
            ),
            const SizedBox(width: 2),
            Icon(Icons.chevron_right, size: 14, color: colors.onSurfaceVariant),
          ],
        ),
      ),
    );
  }
}

/// "How I answered": each step of the turn in plain words.
Future<void> showReceipt(BuildContext context, AnswerReceipt receipt) => showModalBottomSheet<void>(
  context: context,
  builder: (context) => _ReceiptSheet(receipt: receipt),
);

class _ReceiptSheet extends StatelessWidget {
  const _ReceiptSheet({required this.receipt});

  final AnswerReceipt receipt;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final setAside = receipt.memoriesUsed == null ? 0 : receipt.memoriesFound - receipt.memoriesUsed!;

    final looking = [
      if (receipt.plannedNeeds.contains('semantic')) 'things you have told me',
      if (receipt.plannedNeeds.contains('timeline')) 'what happened when',
      if (receipt.plannedNeeds.contains('totals')) 'exact totals',
    ];

    final steps = <(IconData, Color, String, String)>[
      (
        Icons.manage_search,
        colors.primary,
        'Understood the question',
        looking.isEmpty
            ? 'Nothing about you was needed, so I answered from general knowledge.'
            : 'Looked for ${_naturalJoin(looking)}.',
      ),
      if (receipt.plannedNeeds.contains('semantic') || receipt.memoriesFound > 0)
        (
          Icons.psychology_outlined,
          colors.primary,
          receipt.memories == 1 ? 'Used 1 memory' : 'Used ${receipt.memories} memories',
          [
            if (receipt.memoriesFound == 0) 'I did not find anything relevant you have told me.',
            if (setAside > 0) 'Set aside $setAside that did not fit the question.',
            if (receipt.searchedAgain) 'The first search was not enough, so I looked again with different words.',
          ].join(' ').ifEmpty('Only what was relevant to this question.'),
        ),
      if (receipt.timelineEntries > 0)
        (
          Icons.timeline,
          colors.primary,
          'Checked your timeline',
          '${receipt.timelineEntries} recorded ${receipt.timelineEntries == 1 ? 'event' : 'events'} in the period you asked about.',
        ),
      if (receipt.usedTotals)
        (
          Icons.calculate_outlined,
          colors.primary,
          'Counted, not estimated',
          'Figures came from adding up your records, not from the model guessing.',
        ),
      (
        switch (receipt.grounded) {
          true => Icons.verified_outlined,
          false => Icons.report_outlined,
          null => Icons.help_outline,
        },
        switch (receipt.grounded) {
          true => Colors.green.shade600,
          false => colors.error,
          null => colors.onSurfaceVariant,
        },
        switch (receipt.grounded) {
          true => 'Verified',
          false => 'Partly unverified',
          null => 'Not verified',
        },
        switch (receipt.grounded) {
              true => 'Every statement about you was checked against what you have told me.',
              false => "Some statements couldn't be backed up by what you have told me. Treat them with care.",
              null => 'The check could not run this time.',
            } +
            (receipt.rewritten ? ' The first draft was rewritten to stick to the evidence.' : ''),
      ),
    ];

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('How I answered', style: theme.textTheme.titleLarge),
            const SizedBox(height: 16),
            for (final (icon, tone, title, body) in steps)
              Padding(
                padding: const EdgeInsets.only(bottom: 14),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                      width: 36,
                      height: 36,
                      decoration: BoxDecoration(color: tone.withValues(alpha: 0.12), shape: BoxShape.circle),
                      child: Icon(icon, size: 18, color: tone),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(title, style: theme.textTheme.titleSmall),
                          const SizedBox(height: 2),
                          Text(
                            body,
                            style: theme.textTheme.bodySmall?.copyWith(color: colors.onSurfaceVariant, height: 1.4),
                          ),
                        ],
                      ),
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

/// "a", "a and b", "a, b and c".
String _naturalJoin(List<String> items) =>
    items.length < 2 ? items.join() : '${items.sublist(0, items.length - 1).join(', ')} and ${items.last}';

extension on String {
  String ifEmpty(String fallback) => isEmpty ? fallback : this;
}
