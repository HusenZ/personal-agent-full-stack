import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_client.dart';
import '../../core/providers.dart';
import '../chat/chat_repository.dart' show PageOf;

typedef PageOfMemories = PageOf<Memory>;

enum MemoryStatus { active, superseded, archived }

class Memory {
  const Memory({
    required this.id,
    required this.content,
    required this.type,
    required this.status,
    required this.isSensitive,
    required this.createdAt,
    this.occurredAt,
  });

  factory Memory.fromJson(Map<String, dynamic> json) => Memory(
    id: json['id'] as String,
    content: json['content'] as String,
    type: json['memory_type'] as String,
    status: MemoryStatus.values.byName(json['status'] as String),
    isSensitive: json['is_sensitive'] as bool,
    createdAt: DateTime.parse(json['created_at'] as String),
    occurredAt: json['occurred_at'] == null ? null : DateTime.parse(json['occurred_at'] as String),
  );

  final String id;
  final String content;
  final String type;
  final MemoryStatus status;
  final bool isSensitive;
  final DateTime createdAt;
  final DateTime? occurredAt;
}

class MemoriesRepository {
  MemoriesRepository(this._api);

  final ApiClient _api;

  Future<PageOfMemories> list({required bool includeArchived, int page = 1}) async => PageOf.fromResponse(
    await _api.get(
      '/api/v1/memories',
      query: {'include_archived': '$includeArchived', 'page': '$page', 'page_size': '50'},
    ),
    Memory.fromJson,
  );

  Future<Memory> correct(String id, String content) async =>
      Memory.fromJson((await _api.patch('/api/v1/memories/$id', body: {'content': content})).object);

  Future<Memory> setStatus(String id, MemoryStatus status) async =>
      Memory.fromJson((await _api.patch('/api/v1/memories/$id', body: {'status': status.name})).object);

  Future<void> delete(String id) => _api.delete('/api/v1/memories/$id');
}

final memoriesRepositoryProvider = Provider<MemoriesRepository>(
  (ref) => MemoriesRepository(ref.watch(apiClientProvider)),
);
