import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:personal_intelligence/core/api/sse.dart';

Stream<List<int>> chunked(List<int> bytes, int size) async* {
  for (var start = 0; start < bytes.length; start += size) {
    yield bytes.sublist(start, (start + size).clamp(0, bytes.length));
  }
}

Future<List<(String, String)>> parse(Stream<List<int>> bytes) async => [
  await for (final event in parseSse(bytes)) (event.event, event.data),
];

void main() {
  const body =
      'event: query_started\ndata: {"run_id":"r1"}\n\n'
      'event: reply\ndata: {"content":"Café — ✓"}\n\n';

  test('events are read whole, however the bytes are split', () async {
    final bytes = utf8.encode(body);
    // Every chunk size down to one byte, which splits multi-byte characters.
    for (final size in [1, 2, 3, 7, bytes.length]) {
      expect(await parse(chunked(bytes, size)), [
        ('query_started', '{"run_id":"r1"}'),
        ('reply', '{"content":"Café — ✓"}'),
      ], reason: 'chunk size $size');
    }
  });

  test('carriage-return line endings are accepted', () async {
    final events = await parse(Stream.value(utf8.encode('event: a\r\ndata: 1\r\n\r\n')));

    expect(events, [('a', '1')]);
  });

  test('multi-line data is joined with newlines, and comments are ignored', () async {
    final events = await parse(Stream.value(utf8.encode(': keep-alive\ndata: one\ndata: two\n\n')));

    expect(events, [('message', 'one\ntwo')]);
  });

  test('an event cut off by the end of the stream is not emitted', () async {
    final events = await parse(Stream.value(utf8.encode('event: reply\ndata: {"content":"half')));

    expect(events, isEmpty);
  });
}
