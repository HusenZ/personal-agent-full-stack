import 'dart:async';
import 'dart:convert';

/// One server-sent event: its name and its (possibly multi-line) data.
class SseEvent {
  const SseEvent(this.event, this.data);

  final String event;
  final String data;
}

/// Parses a byte stream into server-sent events, per the HTML specification.
///
/// Network chunks fall anywhere, including inside a line or inside a UTF-8
/// character, so bytes are decoded and split into lines before an event is
/// assembled; an event is only emitted at the blank line that ends it. A
/// partial event at the end of the stream is discarded, as the specification
/// requires, rather than emitted half-received.
Stream<SseEvent> parseSse(Stream<List<int>> bytes) async* {
  var event = 'message';
  final data = <String>[];

  await for (final line in bytes.transform(utf8.decoder).transform(const LineSplitter())) {
    if (line.isEmpty) {
      if (data.isNotEmpty) {
        yield SseEvent(event, data.join('\n'));
      }
      event = 'message';
      data.clear();
      continue;
    }
    if (line.startsWith(':')) {
      continue;
    }

    final colon = line.indexOf(':');
    final field = colon == -1 ? line : line.substring(0, colon);
    var value = colon == -1 ? '' : line.substring(colon + 1);
    if (value.startsWith(' ')) {
      value = value.substring(1);
    }

    switch (field) {
      case 'event':
        event = value;
      case 'data':
        data.add(value);
    }
  }
}
