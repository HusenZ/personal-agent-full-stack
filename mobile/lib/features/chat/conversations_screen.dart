import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/ui/components.dart';
import '../../core/ui/feedback.dart';
import '../../core/ui/formatting.dart';
import 'chat_repository.dart';
import 'conversations_controller.dart';

/// Ways into a first conversation, filled into the composer rather than sent,
/// so the user can make them their own.
const starterPrompts = [
  'What should I focus on this week?',
  'Help me plan progress on my goals',
  'What do you know about me so far?',
  'I want to build a new habit',
];

/// Explains what deleting a chat does and does not remove, and asks first.
Future<bool> confirmDeleteConversation(BuildContext context) => confirm(
  context,
  title: 'Delete this conversation?',
  message:
      'Its messages are removed permanently. Anything I learned from it stays in Memory, '
      'where you can review or delete it.',
  action: 'Delete',
  destructive: true,
);

class ConversationsScreen extends ConsumerWidget {
  const ConversationsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final conversations = ref.watch(conversationsProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Chats')),
      floatingActionButton: FloatingActionButton.extended(
        key: const Key('new-chat'),
        onPressed: () => context.push('/chats/new'),
        icon: const Icon(Icons.add_comment_outlined),
        label: const Text('New chat'),
      ),
      body: switch (conversations) {
        AsyncData(:final value) when value.items.isEmpty => const _Empty(),
        AsyncData(:final value) => RefreshIndicator(
          onRefresh: () => ref.refresh(conversationsProvider.future),
          child: NotificationListener<ScrollNotification>(
            onNotification: (notification) {
              if (notification.metrics.extentAfter < 300) {
                ref.read(conversationsProvider.notifier).loadMore();
              }
              return false;
            },
            child: _List(list: value),
          ),
        ),
        AsyncError(:final error) => ErrorView(error: error, onRetry: () => ref.invalidate(conversationsProvider)),
        _ => const Center(child: CircularProgressIndicator()),
      },
    );
  }
}

class _List extends ConsumerWidget {
  const _List({required this.list});

  final ConversationList list;

  Future<bool> _delete(BuildContext context, WidgetRef ref, ConversationSummary conversation) async {
    if (!await confirmDeleteConversation(context)) {
      return false;
    }
    try {
      await ref.read(chatRepositoryProvider).deleteConversation(conversation.id);
      ref.read(conversationsProvider.notifier).remove(conversation.id);
      if (context.mounted) {
        showMessage(context, 'Conversation deleted');
      }
      return true;
    } on Object catch (error) {
      if (context.mounted) {
        showError(context, error);
      }
      return false;
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final now = DateTime.now();
    final rows = <Widget>[];
    String? group;
    for (final conversation in list.items) {
      final heading = recencyGroup(conversation.lastMessageAt, now: now);
      if (heading != group) {
        group = heading;
        rows.add(SectionHeader(heading));
      }
      rows.add(
        Dismissible(
          key: ValueKey(conversation.id),
          direction: DismissDirection.endToStart,
          background: const _DeleteBackground(),
          confirmDismiss: (_) => _delete(context, ref, conversation),
          child: _ConversationTile(conversation: conversation, now: now),
        ),
      );
    }
    if (list.hasMore) {
      rows.add(
        const Padding(
          padding: EdgeInsets.all(16),
          child: Center(child: CircularProgressIndicator()),
        ),
      );
    }
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 104),
      children: rows,
    );
  }
}

class _DeleteBackground extends StatelessWidget {
  const _DeleteBackground();

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.only(right: 24),
      alignment: Alignment.centerRight,
      decoration: BoxDecoration(color: colors.errorContainer, borderRadius: BorderRadius.circular(20)),
      child: Icon(Icons.delete_outline, color: colors.onErrorContainer),
    );
  }
}

class _ConversationTile extends StatelessWidget {
  const _ConversationTile({required this.conversation, required this.now});

  final ConversationSummary conversation;
  final DateTime now;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final initial = conversation.title.trim().isEmpty ? '?' : conversation.title.trim()[0].toUpperCase();
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Card(
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: () => context.push('/chats/${conversation.id}'),
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Row(
              children: [
                CircleAvatar(
                  radius: 22,
                  backgroundColor: colors.secondaryContainer,
                  foregroundColor: colors.onSecondaryContainer,
                  child: Text(
                    initial,
                    style: theme.textTheme.titleMedium?.copyWith(color: colors.onSecondaryContainer),
                  ),
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        conversation.title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.titleMedium,
                      ),
                      const SizedBox(height: 2),
                      Text(
                        conversation.messageCount == 1 ? '1 message' : '${conversation.messageCount} messages',
                        style: theme.textTheme.bodySmall?.copyWith(color: colors.onSurfaceVariant),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  formatTimestamp(conversation.lastMessageAt, now: now),
                  style: theme.textTheme.labelSmall?.copyWith(color: colors.onSurfaceVariant),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty();

  @override
  Widget build(BuildContext context) => EmptyState(
    icon: Icons.forum_outlined,
    title: 'Start your first conversation',
    message: 'Ask about your goals, your week, or anything on your mind. I answer from what you have told me.',
    action: Wrap(
      alignment: WrapAlignment.center,
      spacing: 8,
      runSpacing: 8,
      children: [
        for (final prompt in starterPrompts)
          ActionChip(
            label: Text(prompt),
            onPressed: () => context.push('/chats/new', extra: prompt),
          ),
      ],
    ),
  );
}
