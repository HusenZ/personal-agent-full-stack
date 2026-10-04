import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_client.dart';
import '../../core/api/api_exception.dart';
import '../../core/providers.dart';

class OnboardingTurn {
  const OnboardingTurn({
    required this.isComplete,
    required this.questionsAsked,
    required this.overallConfidence,
    this.question,
  });

  factory OnboardingTurn.fromJson(Map<String, dynamic> json) => OnboardingTurn(
    isComplete: json['is_complete'] as bool,
    questionsAsked: json['questions_asked'] as int,
    overallConfidence: (json['overall_confidence'] as num).toDouble(),
    question: (json['question'] as Map<String, dynamic>?)?['text'] as String?,
  );

  final bool isComplete;
  final int questionsAsked;
  final double overallConfidence;
  final String? question;
}

/// Where onboarding stands for the signed-in user.
enum OnboardingState { notStarted, inProgress, complete }

class OnboardingRepository {
  OnboardingRepository(this._api);

  final ApiClient _api;

  Future<OnboardingState> state() async {
    final turn = await status();
    return switch (turn) {
      null => OnboardingState.notStarted,
      OnboardingTurn(isComplete: true) => OnboardingState.complete,
      _ => OnboardingState.inProgress,
    };
  }

  /// The interview's progress, or null if it has not been started.
  Future<OnboardingTurn?> status() async {
    try {
      return OnboardingTurn.fromJson((await _api.get('/api/v1/onboarding/status')).object);
    } on ApiException catch (error) {
      if (error.code == 'ONBOARDING_NOT_STARTED') {
        return null;
      }
      rethrow;
    }
  }

  /// Begins the interview, or resumes the one in progress.
  Future<OnboardingTurn> start() async => OnboardingTurn.fromJson((await _api.post('/api/v1/onboarding/start')).object);

  Future<OnboardingTurn> answer(String answer) async =>
      OnboardingTurn.fromJson((await _api.post('/api/v1/onboarding/message', body: {'answer': answer})).object);
}

final onboardingRepositoryProvider = Provider<OnboardingRepository>(
  (ref) => OnboardingRepository(ref.watch(apiClientProvider)),
);

final onboardingStatusProvider = FutureProvider.autoDispose<OnboardingTurn?>(
  (ref) => ref.watch(onboardingRepositoryProvider).status(),
);
