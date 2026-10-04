import 'package:flutter/material.dart';

/// The app's mark: a gradient tile with the assistant's sparkle.
class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.size = 72});

  final double size;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return ExcludeSemantics(
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(size * 0.3),
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [colors.primary, colors.tertiary],
          ),
          boxShadow: [
            BoxShadow(
              color: colors.primary.withValues(alpha: 0.3),
              blurRadius: size * 0.35,
              offset: Offset(0, size * 0.12),
            ),
          ],
        ),
        child: Icon(Icons.auto_awesome, color: colors.onPrimary, size: size * 0.5),
      ),
    );
  }
}

/// The assistant's small round avatar beside its messages.
class AssistantAvatar extends StatelessWidget {
  const AssistantAvatar({super.key, this.size = 30});

  final double size;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return ExcludeSemantics(
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: LinearGradient(colors: [colors.primary, colors.tertiary]),
        ),
        child: Icon(Icons.auto_awesome, size: size * 0.55, color: colors.onPrimary),
      ),
    );
  }
}

/// A centred icon, title and explanation, with an optional action: for empty
/// lists and screens that failed to load.
class EmptyState extends StatelessWidget {
  const EmptyState({super.key, required this.icon, required this.title, this.message, this.action, this.tone});

  final IconData icon;
  final String title;
  final String? message;
  final Widget? action;

  /// The icon's colour; the primary colour if not given.
  final Color? tone;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final color = tone ?? theme.colorScheme.primary;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(32),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 360),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 80,
                height: 80,
                decoration: BoxDecoration(color: color.withValues(alpha: 0.12), shape: BoxShape.circle),
                child: Icon(icon, size: 36, color: color),
              ),
              const SizedBox(height: 20),
              Text(title, style: theme.textTheme.titleLarge, textAlign: TextAlign.center),
              if (message != null) ...[
                const SizedBox(height: 8),
                Text(
                  message!,
                  textAlign: TextAlign.center,
                  style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant, height: 1.4),
                ),
              ],
              if (action != null) ...[const SizedBox(height: 24), action!],
            ],
          ),
        ),
      ),
    );
  }
}

/// A small coloured label, e.g. a memory's type or a claim's kind.
class Tag extends StatelessWidget {
  const Tag({super.key, required this.label, required this.color, this.icon});

  final String label;
  final Color color;
  final IconData? icon;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
    decoration: BoxDecoration(color: color.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(8)),
    child: Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (icon != null) ...[Icon(icon, size: 13, color: color), const SizedBox(width: 4)],
        Text(
          label,
          style: Theme.of(context).textTheme.labelSmall?.copyWith(color: color, fontWeight: FontWeight.w700),
        ),
      ],
    ),
  );
}

/// A section title inside a scrolling page.
class SectionHeader extends StatelessWidget {
  const SectionHeader(this.title, {super.key, this.trailing});

  final String title;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 24, 4, 10),
      child: Row(
        children: [
          Expanded(
            child: Text(
              title.toUpperCase(),
              style: theme.textTheme.labelMedium?.copyWith(
                color: theme.colorScheme.primary,
                fontWeight: FontWeight.w700,
                letterSpacing: 1.1,
              ),
            ),
          ),
          ?trailing,
        ],
      ),
    );
  }
}

/// A spinner inside a filled button, sized to its text.
class ButtonProgress extends StatelessWidget {
  const ButtonProgress({super.key});

  @override
  Widget build(BuildContext context) => SizedBox.square(
    dimension: 22,
    child: CircularProgressIndicator(strokeWidth: 2.5, color: Theme.of(context).colorScheme.onPrimary),
  );
}
