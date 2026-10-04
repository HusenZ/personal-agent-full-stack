import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/ui/components.dart';
import '../../core/ui/feedback.dart';
import '../../core/ui/formatting.dart';
import 'memories_repository.dart';

final showArchivedProvider = NotifierProvider<_ShowArchived, bool>(_ShowArchived.new);

class _ShowArchived extends Notifier<bool> {
  @override
  bool build() => false;

  void toggle() => state = !state;
}

final memoriesProvider = FutureProvider.autoDispose<List<Memory>>((ref) async {
  final includeArchived = ref.watch(showArchivedProvider);
  final repository = ref.watch(memoriesRepositoryProvider);
  final memories = <Memory>[];
  var page = 1;
  // Loads everything in pages; the list is for reviewing.
  while (page <= 20) {
    final result = await repository.list(includeArchived: includeArchived, page: page);
    memories.addAll(result.items);
    if (!result.hasNext) {
      break;
    }
    page++;
  }
  return memories;
});

/// How each kind of memory is shown. Unknown kinds, from a newer server,
/// fall back to a neutral look rather than failing.
(IconData, Color) memoryLook(String type, ColorScheme colors) => switch (type) {
  'fact' => (Icons.lightbulb_outline, colors.primary),
  'goal' => (Icons.flag_outlined, Colors.green.shade600),
  'preference' => (Icons.favorite_border, Colors.pink.shade400),
  'constraint' => (Icons.block_outlined, Colors.orange.shade700),
  'event' => (Icons.event_outlined, Colors.blue.shade600),
  'experience' => (Icons.auto_stories_outlined, Colors.teal.shade600),
  'observation' => (Icons.visibility_outlined, Colors.indigo.shade400),
  'plan' => (Icons.checklist, Colors.deepPurple.shade400),
  'temporary_context' => (Icons.schedule, Colors.brown.shade400),
  _ => (Icons.notes, colors.outline),
};

/// What the assistant remembers, and the user's control over it.
class MemoriesScreen extends ConsumerStatefulWidget {
  const MemoriesScreen({super.key});

  @override
  ConsumerState<MemoriesScreen> createState() => _MemoriesScreenState();
}

class _MemoriesScreenState extends ConsumerState<MemoriesScreen> {
  final _search = TextEditingController();
  String? _type;

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  List<Memory> _visible(List<Memory> memories) {
    final query = _search.text.trim().toLowerCase();
    return [
      for (final memory in memories)
        if ((_type == null || memory.type == _type) && (query.isEmpty || memory.content.toLowerCase().contains(query)))
          memory,
    ];
  }

  @override
  Widget build(BuildContext context) {
    final memories = ref.watch(memoriesProvider);
    final showArchived = ref.watch(showArchivedProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('What I remember')),
      body: switch (memories) {
        AsyncData(:final value) when value.isEmpty && !showArchived => const EmptyState(
          icon: Icons.psychology_outlined,
          title: 'Nothing remembered yet',
          message:
              'Things you mention in conversation that are worth keeping appear here, '
              'and you can correct or delete any of them.',
        ),
        AsyncData(:final value) => _content(value, showArchived),
        AsyncError(:final error) => ErrorView(error: error, onRetry: () => ref.invalidate(memoriesProvider)),
        _ => const Center(child: CircularProgressIndicator()),
      },
    );
  }

  Widget _content(List<Memory> memories, bool showArchived) {
    final types = {for (final memory in memories) memory.type}.toList()..sort();
    final visible = _visible(memories);

    return RefreshIndicator(
      onRefresh: () => ref.refresh(memoriesProvider.future),
      child: CustomScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        slivers: [
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
              child: TextField(
                key: const Key('memory-search'),
                controller: _search,
                onChanged: (_) => setState(() {}),
                textInputAction: TextInputAction.search,
                decoration: InputDecoration(
                  hintText: 'Search memories',
                  prefixIcon: const Icon(Icons.search),
                  suffixIcon: _search.text.isEmpty
                      ? null
                      : IconButton(
                          tooltip: 'Clear search',
                          icon: const Icon(Icons.close),
                          onPressed: () => setState(_search.clear),
                        ),
                ),
              ),
            ),
          ),
          SliverToBoxAdapter(
            child: SizedBox(
              height: 48,
              child: ListView(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: 16),
                children: [
                  _filter(label: 'All', selected: _type == null, onSelected: () => setState(() => _type = null)),
                  for (final type in types)
                    _filter(
                      label: humanise(type),
                      selected: _type == type,
                      onSelected: () => setState(() => _type = _type == type ? null : type),
                    ),
                  const SizedBox(width: 8),
                  FilterChip(
                    key: const Key('show-archived'),
                    avatar: const Icon(Icons.inventory_2_outlined, size: 18),
                    label: const Text('Archived'),
                    selected: showArchived,
                    onSelected: (_) => ref.read(showArchivedProvider.notifier).toggle(),
                  ),
                ],
              ),
            ),
          ),
          if (visible.isEmpty)
            const SliverFillRemaining(
              hasScrollBody: false,
              child: EmptyState(icon: Icons.search_off, title: 'No matching memories'),
            )
          else
            SliverPadding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
              sliver: SliverList.separated(
                itemCount: visible.length,
                separatorBuilder: (_, _) => const SizedBox(height: 8),
                itemBuilder: (context, index) => _MemoryTile(memory: visible[index]),
              ),
            ),
        ],
      ),
    );
  }

  Widget _filter({required String label, required bool selected, required VoidCallback onSelected}) => Padding(
    padding: const EdgeInsets.only(right: 8),
    child: ChoiceChip(label: Text(label), selected: selected, onSelected: (_) => onSelected()),
  );
}

class _MemoryTile extends ConsumerWidget {
  const _MemoryTile({required this.memory});

  final Memory memory;

  Future<bool> _act(BuildContext context, WidgetRef ref, Future<void> Function() action) async {
    try {
      await action();
      ref.invalidate(memoriesProvider);
      return true;
    } on Object catch (error) {
      if (context.mounted) {
        showError(context, error);
      }
      return false;
    }
  }

  Future<void> _edit(BuildContext context, WidgetRef ref) async {
    final controller = TextEditingController(text: memory.content);
    final corrected = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Correct this memory'),
        content: TextField(
          controller: controller,
          maxLength: 1000,
          minLines: 2,
          maxLines: 6,
          autofocus: true,
          textCapitalization: TextCapitalization.sentences,
        ),
        actions: [
          TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(minimumSize: const Size(88, 44)),
            onPressed: () => Navigator.of(context).pop(controller.text.trim()),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (corrected == null || corrected.isEmpty || corrected == memory.content || !context.mounted) {
      return;
    }
    if (await _act(context, ref, () => ref.read(memoriesRepositoryProvider).correct(memory.id, corrected)) &&
        context.mounted) {
      showMessage(context, 'Memory updated. The earlier version is kept in its history.');
    }
  }

  Future<void> _setArchived(BuildContext context, WidgetRef ref, {required bool archived}) async {
    final repository = ref.read(memoriesRepositoryProvider);
    final messenger = ScaffoldMessenger.of(context);
    final status = archived ? MemoryStatus.archived : MemoryStatus.active;
    if (!await _act(context, ref, () => repository.setStatus(memory.id, status))) {
      return;
    }
    messenger
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text(archived ? 'Archived. It will no longer shape answers.' : 'Restored.'),
          action: SnackBarAction(
            label: 'Undo',
            onPressed: () async {
              try {
                await repository.setStatus(memory.id, archived ? MemoryStatus.active : MemoryStatus.archived);
                ref.invalidate(memoriesProvider);
              } on Object catch (error) {
                messenger.showSnackBar(SnackBar(content: Text(messageFor(error))));
              }
            },
          ),
        ),
      );
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final archived = memory.status == MemoryStatus.archived;
    final (icon, tone) = memoryLook(memory.type, colors);

    return Opacity(
      opacity: archived ? 0.6 : 1,
      child: Card(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 4, 12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(color: tone.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(12)),
                child: Icon(icon, size: 20, color: tone),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(memory.content, style: theme.textTheme.bodyLarge?.copyWith(height: 1.35)),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        Tag(label: humanise(memory.type), color: tone),
                        if (memory.isSensitive) Tag(label: 'Sensitive', color: colors.error, icon: Icons.lock_outline),
                        if (archived) Tag(label: 'Archived', color: colors.outline),
                        Text(
                          formatTimestamp(memory.occurredAt ?? memory.createdAt),
                          style: theme.textTheme.labelSmall?.copyWith(color: colors.onSurfaceVariant),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              PopupMenuButton<String>(
                tooltip: 'Options',
                onSelected: (choice) async {
                  switch (choice) {
                    case 'edit':
                      await _edit(context, ref);
                    case 'archive':
                      await _setArchived(context, ref, archived: !archived);
                    case 'delete':
                      final sure = await confirm(
                        context,
                        title: 'Delete this memory?',
                        message: 'It will be removed permanently, with its history. This cannot be undone.',
                        action: 'Delete',
                        destructive: true,
                      );
                      if (sure && context.mounted) {
                        await _act(context, ref, () => ref.read(memoriesRepositoryProvider).delete(memory.id));
                      }
                  }
                },
                itemBuilder: (context) => [
                  if (!archived)
                    const PopupMenuItem(
                      value: 'edit',
                      child: ListTile(leading: Icon(Icons.edit_outlined), title: Text('Correct')),
                    ),
                  PopupMenuItem(
                    value: 'archive',
                    child: ListTile(
                      leading: Icon(archived ? Icons.unarchive_outlined : Icons.archive_outlined),
                      title: Text(archived ? 'Restore' : 'Archive'),
                    ),
                  ),
                  PopupMenuItem(
                    value: 'delete',
                    child: ListTile(
                      leading: Icon(Icons.delete_outline, color: colors.error),
                      title: Text('Delete', style: TextStyle(color: colors.error)),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
