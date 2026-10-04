import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'chat_repository.dart';

class ConversationList {
  const ConversationList({required this.items, required this.page, required this.hasMore, this.loadingMore = false});

  final List<ConversationSummary> items;
  final int page;
  final bool hasMore;
  final bool loadingMore;
}

/// The user's conversations, most recent first, a page at a time.
class ConversationsController extends AsyncNotifier<ConversationList> {
  @override
  Future<ConversationList> build() async {
    final first = await ref.read(chatRepositoryProvider).conversations();
    return ConversationList(items: first.items, page: first.page, hasMore: first.hasNext);
  }

  /// Drops a deleted conversation from what is shown, without a refetch, so a
  /// swiped-away row is gone at once.
  void remove(String id) {
    final current = state.value;
    if (current == null) {
      return;
    }
    state = AsyncData(
      ConversationList(
        items: [
          for (final item in current.items)
            if (item.id != id) item,
        ],
        page: current.page,
        hasMore: current.hasMore,
        loadingMore: current.loadingMore,
      ),
    );
  }

  Future<void> loadMore() async {
    final current = state.value;
    if (current == null || !current.hasMore || current.loadingMore) {
      return;
    }
    state = AsyncData(ConversationList(items: current.items, page: current.page, hasMore: true, loadingMore: true));
    try {
      final next = await ref.read(chatRepositoryProvider).conversations(page: current.page + 1);
      state = AsyncData(
        ConversationList(items: [...current.items, ...next.items], page: next.page, hasMore: next.hasNext),
      );
    } on Object {
      // Keep what is shown; scrolling to the end again retries.
      state = AsyncData(current);
    }
  }
}

final conversationsProvider = AsyncNotifierProvider<ConversationsController, ConversationList>(
  ConversationsController.new,
);
