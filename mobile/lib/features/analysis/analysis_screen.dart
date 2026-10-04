import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_client.dart';
import '../../core/api/api_exception.dart';
import '../../core/providers.dart';
import '../../core/ui/components.dart';
import '../../core/ui/feedback.dart';
import '../../core/ui/formatting.dart';

class Claim {
  const Claim(this.type, this.content, this.confidence);

  final String type;
  final String content;
  final double confidence;
}

class Analysis {
  const Analysis({required this.createdAt, required this.sections, required this.degraded});

  factory Analysis.fromJson(Map<String, dynamic> json) => Analysis(
    createdAt: DateTime.parse(json['created_at'] as String),
    degraded: json['degraded'] as bool,
    sections: {
      for (final entry in (json['sections'] as Map<String, dynamic>).entries)
        entry.key: [
          for (final claim in entry.value as List<dynamic>)
            Claim(
              (claim as Map<String, dynamic>)['claim_type'] as String,
              claim['content'] as String,
              (claim['confidence'] as num).toDouble(),
            ),
        ],
    },
  );

  final DateTime createdAt;
  final Map<String, List<Claim>> sections;
  final bool degraded;
}

/// The order the backend defines its sections in, which reads as a narrative.
const _sectionOrder = [
  'current_situation',
  'goals',
  'strengths',
  'constraints',
  'bottlenecks',
  'priorities',
  'suggested_actions',
];

final analysisProvider = FutureProvider.autoDispose<Analysis?>((ref) async {
  try {
    return Analysis.fromJson((await ref.watch(apiClientProvider).get('/api/v1/analysis/latest')).object);
  } on ApiException catch (error) {
    if (error.code == 'ANALYSIS_NOT_FOUND') {
      return null;
    }
    rethrow;
  }
});

class AnalysisScreen extends ConsumerStatefulWidget {
  const AnalysisScreen({super.key});

  @override
  ConsumerState<AnalysisScreen> createState() => _AnalysisScreenState();
}

class _AnalysisScreenState extends ConsumerState<AnalysisScreen> {
  bool _generating = false;

  Future<void> _generate(ApiClient api) async {
    setState(() => _generating = true);
    try {
      await api.post('/api/v1/analysis');
      ref.invalidate(analysisProvider);
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

  @override
  Widget build(BuildContext context) {
    final analysis = ref.watch(analysisProvider);
    final api = ref.read(apiClientProvider);
    final theme = Theme.of(context);
    final colors = theme.colorScheme;

    final generate = FilledButton.icon(
      key: const Key('generate-analysis'),
      onPressed: _generating ? null : () => _generate(api),
      icon: _generating ? const ButtonProgress() : const Icon(Icons.auto_awesome),
      label: Text(_generating ? 'Reading everything…' : 'Generate a new analysis'),
    );

    return Scaffold(
      appBar: AppBar(title: const Text('Your analysis')),
      body: switch (analysis) {
        AsyncData(value: null) => EmptyState(
          icon: Icons.insights_outlined,
          title: 'No analysis yet',
          message:
              'An analysis reads everything known about you and says what stands out, '
              'each point marked as a fact, an inference or a suggestion.',
          action: generate,
        ),
        AsyncData(value: final Analysis value) => ListView(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
          children: [
            Row(
              children: [
                Icon(Icons.schedule, size: 16, color: colors.onSurfaceVariant),
                const SizedBox(width: 6),
                Text(
                  'Generated ${formatTimestamp(value.createdAt)}',
                  style: theme.textTheme.bodySmall?.copyWith(color: colors.onSurfaceVariant),
                ),
              ],
            ),
            if (value.degraded) ...[
              const SizedBox(height: 12),
              Card(
                color: colors.tertiaryContainer,
                child: Padding(
                  padding: const EdgeInsets.all(14),
                  child: Row(
                    children: [
                      Icon(Icons.info_outline, color: colors.onTertiaryContainer),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Text(
                          'This analysis is thinner than usual: too little was known to say more.',
                          style: TextStyle(color: colors.onTertiaryContainer),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
            const SizedBox(height: 4),
            const _Legend(),
            for (final section in _sectionOrder)
              if (value.sections[section] case final claims? when claims.isNotEmpty) ...[
                SectionHeader(humanise(section)),
                Card(
                  child: Column(
                    children: [
                      for (final (index, claim) in claims.indexed) ...[
                        if (index > 0) const Divider(indent: 16, endIndent: 16),
                        _ClaimRow(claim: claim),
                      ],
                    ],
                  ),
                ),
              ],
            const SizedBox(height: 28),
            generate,
          ],
        ),
        AsyncError(:final error) => ErrorView(error: error, onRetry: () => ref.invalidate(analysisProvider)),
        _ => const Center(child: CircularProgressIndicator()),
      },
    );
  }
}

/// How firmly each kind of claim is asserted, shown so a guess is never read
/// as a fact.
(IconData, Color) claimLook(String type, ColorScheme colors) => switch (type) {
  'fact' => (Icons.verified_outlined, Colors.green.shade600),
  'observation' => (Icons.visibility_outlined, Colors.blue.shade600),
  'inference' => (Icons.psychology_alt_outlined, Colors.deepPurple.shade400),
  'recommendation' => (Icons.tips_and_updates_outlined, Colors.orange.shade700),
  'uncertainty' => (Icons.help_outline, colors.outline),
  _ => (Icons.notes, colors.outline),
};

class _Legend extends StatelessWidget {
  const _Legend();

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Wrap(
        spacing: 6,
        runSpacing: 6,
        children: [
          for (final type in ['fact', 'observation', 'inference', 'recommendation', 'uncertainty'])
            Tag(label: humanise(type), color: claimLook(type, colors).$2, icon: claimLook(type, colors).$1),
        ],
      ),
    );
  }
}

class _ClaimRow extends StatelessWidget {
  const _ClaimRow({required this.claim});

  final Claim claim;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final (icon, tone) = claimLook(claim.type, colors);
    final confidence = claim.confidence.clamp(0.0, 1.0);
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(claim.content, style: theme.textTheme.bodyLarge?.copyWith(height: 1.4)),
          const SizedBox(height: 10),
          Row(
            children: [
              Tag(label: humanise(claim.type), color: tone, icon: icon),
              const SizedBox(width: 12),
              Expanded(
                child: Semantics(
                  label: 'Confidence ${(confidence * 100).round()}%',
                  child: ExcludeSemantics(
                    child: LinearProgressIndicator(
                      value: confidence,
                      minHeight: 5,
                      borderRadius: BorderRadius.circular(4),
                      color: tone,
                      backgroundColor: tone.withValues(alpha: 0.15),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Text(
                '${(confidence * 100).round()}%',
                style: theme.textTheme.labelSmall?.copyWith(color: colors.onSurfaceVariant),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
