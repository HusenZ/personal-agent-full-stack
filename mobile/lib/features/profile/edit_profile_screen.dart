import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/ui/components.dart';
import '../../core/ui/feedback.dart';
import '../../core/ui/formatting.dart';
import 'profile_repository.dart';

/// Edits the whole profile and saves it in one request, matching the API's
/// replace-each-collection semantics.
class EditProfileScreen extends ConsumerStatefulWidget {
  const EditProfileScreen({super.key, required this.initial});

  final Profile initial;

  @override
  ConsumerState<EditProfileScreen> createState() => _EditProfileScreenState();
}

class _EditProfileScreenState extends ConsumerState<EditProfileScreen> {
  late final _timezone = TextEditingController(text: widget.initial.timezone);
  late final List<(TextEditingController, GoalPriority)> _goals = [
    for (final goal in widget.initial.goals) (TextEditingController(text: goal.name), goal.priority),
  ];
  late final List<TextEditingController> _constraints = [
    for (final item in widget.initial.constraints) TextEditingController(text: item),
  ];
  late final List<(TextEditingController, TextEditingController)> _preferences = [
    for (final entry in widget.initial.preferences.entries)
      (TextEditingController(text: entry.key), TextEditingController(text: entry.value)),
  ];
  bool _saving = false;

  @override
  void dispose() {
    _timezone.dispose();
    for (final (name, _) in _goals) {
      name.dispose();
    }
    for (final item in _constraints) {
      item.dispose();
    }
    for (final (key, value) in _preferences) {
      key.dispose();
      value.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    final profile = Profile(
      timezone: _timezone.text.trim().isEmpty ? 'UTC' : _timezone.text.trim(),
      goals: [
        for (final (name, priority) in _goals)
          if (name.text.trim().isNotEmpty) Goal(name.text.trim(), priority),
      ],
      constraints: [
        for (final item in _constraints)
          if (item.text.trim().isNotEmpty) item.text.trim(),
      ],
      preferences: {
        for (final (key, value) in _preferences)
          if (key.text.trim().isNotEmpty && value.text.trim().isNotEmpty)
            key.text.trim().toLowerCase().replaceAll(' ', '_'): value.text.trim(),
      },
    );
    try {
      await ref.read(profileRepositoryProvider).save(profile);
      ref.invalidate(profileProvider);
      if (mounted) {
        context.pop();
      }
    } on Object catch (error) {
      if (mounted) {
        showError(context, error);
      }
    } finally {
      if (mounted) {
        setState(() => _saving = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    Widget heading(String text, String addLabel, VoidCallback onAdd) => SectionHeader(
      text,
      trailing: TextButton.icon(onPressed: onAdd, icon: const Icon(Icons.add, size: 18), label: Text(addLabel)),
    );
    Widget hint(String text, bool show) => show
        ? Padding(
            padding: const EdgeInsets.only(left: 4, bottom: 8),
            child: Text(text, style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
          )
        : const SizedBox.shrink();

    return Scaffold(
      appBar: AppBar(
        title: const Text('Edit profile'),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: FilledButton(
              style: FilledButton.styleFrom(minimumSize: const Size(80, 40)),
              onPressed: _saving ? null : _save,
              child: _saving ? const ButtonProgress() : const Text('Save'),
            ),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          heading(
            'Goals',
            'Add goal',
            () => setState(() => _goals.add((TextEditingController(), GoalPriority.medium))),
          ),
          hint('What are you working towards?', _goals.isEmpty),
          for (final (index, (name, priority)) in _goals.indexed)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: name,
                      maxLength: 500,
                      decoration: const InputDecoration(counterText: '', hintText: 'e.g. Run a half marathon'),
                    ),
                  ),
                  const SizedBox(width: 8),
                  DropdownButton<GoalPriority>(
                    underline: const SizedBox.shrink(),
                    borderRadius: BorderRadius.circular(12),
                    value: priority,
                    items: [
                      for (final option in GoalPriority.values)
                        DropdownMenuItem(value: option, child: Text(humanise(option.name))),
                    ],
                    onChanged: (value) => setState(() => _goals[index] = (name, value ?? priority)),
                  ),
                  IconButton(
                    tooltip: 'Remove',
                    icon: const Icon(Icons.close),
                    onPressed: () => setState(() => _goals.removeAt(index).$1.dispose()),
                  ),
                ],
              ),
            ),
          heading('Constraints', 'Add', () => setState(() => _constraints.add(TextEditingController()))),
          hint('Anything that limits what you can do, like time or budget.', _constraints.isEmpty),
          for (final (index, item) in _constraints.indexed)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: item,
                      maxLength: 2000,
                      decoration: const InputDecoration(
                        counterText: '',
                        hintText: 'e.g. Only free on weekday evenings',
                      ),
                    ),
                  ),
                  IconButton(
                    tooltip: 'Remove',
                    icon: const Icon(Icons.close),
                    onPressed: () => setState(() => _constraints.removeAt(index).dispose()),
                  ),
                ],
              ),
            ),
          heading(
            'Preferences',
            'Add',
            () => setState(() => _preferences.add((TextEditingController(), TextEditingController()))),
          ),
          hint('How you like answers, e.g. tone: direct.', _preferences.isEmpty),
          for (final (index, (key, value)) in _preferences.indexed)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: key,
                      decoration: const InputDecoration(hintText: 'e.g. tone'),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: TextField(
                      controller: value,
                      decoration: const InputDecoration(hintText: 'e.g. direct'),
                    ),
                  ),
                  IconButton(
                    tooltip: 'Remove',
                    icon: const Icon(Icons.close),
                    onPressed: () => setState(() {
                      final (removedKey, removedValue) = _preferences.removeAt(index);
                      removedKey.dispose();
                      removedValue.dispose();
                    }),
                  ),
                ],
              ),
            ),
          const SectionHeader('Time zone'),
          TextField(
            controller: _timezone,
            decoration: const InputDecoration(
              prefixIcon: Icon(Icons.public),
              helperText: 'Decides what "last week" means, e.g. Europe/London',
            ),
          ),
        ],
      ),
    );
  }
}
