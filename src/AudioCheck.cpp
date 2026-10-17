// SPDX-License-Identifier: GPL-2.0-only
#include <algorithm>
#include <alsa/asoundlib.h>
#include <array>
#include <cerrno>
#include <charconv>
#include <chrono>
#include <cstdint>
#include <expected>
#include <iostream>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;
using Parameters = std::unique_ptr<snd_pcm_hw_params_t, decltype(&snd_pcm_hw_params_free)>;
using SoftwareParameters = std::unique_ptr<snd_pcm_sw_params_t, decltype(&snd_pcm_sw_params_free)>;
constexpr std::array<unsigned int, 4> rates = {44100, 48000, 96000, 192000};
constexpr unsigned int highest_rate = rates.back();
constexpr auto transfer_timeout = std::chrono::seconds(4);
constexpr auto minimum_duration = std::chrono::milliseconds(800);
constexpr auto maximum_duration = std::chrono::seconds(2);
constexpr int poll_timeout_ms = 100;
constexpr unsigned int periods_per_second = 100;
constexpr snd_pcm_uframes_t periods = 4;

struct ClosePcm {
  void operator()(snd_pcm_t *pcm) const noexcept {
    snd_pcm_drop(pcm);
    snd_pcm_close(pcm);
  }
};

using Pcm = std::unique_ptr<snd_pcm_t, ClosePcm>;

struct Options {
  unsigned int rate;
  snd_pcm_stream_t stream;
  bool prepare_only;
  std::string device;
};

std::optional<Options> options(int argc, char **argv) {
  constexpr int minimum_arguments = 3;
  constexpr int maximum_arguments = 4;
  if (argc < minimum_arguments || argc > maximum_arguments) { return std::nullopt; }
  const std::string_view rate_text(argv[1]);
  unsigned int rate = 0;
  const auto parsed = std::from_chars(rate_text.data(), rate_text.data() + rate_text.size(), rate);
  if (parsed.ec != std::errc{} || parsed.ptr != rate_text.data() + rate_text.size() ||
      std::ranges::find(rates, rate) == rates.end()) {
    return std::nullopt;
  }
  const std::string_view mode(argv[2]);
  if (mode != "capture" && mode != "playback" && mode != "prepare-capture" &&
      mode != "prepare-playback") {
    return std::nullopt;
  }
  return Options{.rate = rate,
                 .stream =
                     mode.ends_with("capture") ? SND_PCM_STREAM_CAPTURE : SND_PCM_STREAM_PLAYBACK,
                 .prepare_only = mode.starts_with("prepare-"),
                 .device = argc == maximum_arguments ? argv[3] : "hw:CARD=OCTACAPTURE,DEV=0"};
}

unsigned int channels(const Options &configuration) {
  if (configuration.rate == highest_rate) { return 4; }
  return configuration.stream == SND_PCM_STREAM_CAPTURE ? 12U : 10U;
}

int configure_software(snd_pcm_t *pcm, const Options &configuration) {
  snd_pcm_sw_params_t *raw = nullptr;
  int result = snd_pcm_sw_params_malloc(&raw);
  const SoftwareParameters parameters(raw, &snd_pcm_sw_params_free);
  if (result < 0) { return result; }
  result = snd_pcm_sw_params_current(pcm, parameters.get());
  if (result < 0) { return result; }
  const auto period = configuration.rate / periods_per_second;
  result = snd_pcm_sw_params_set_avail_min(pcm, parameters.get(), period);
  if (result < 0) { return result; }
  const auto start = configuration.stream == SND_PCM_STREAM_PLAYBACK ? period * periods : 1;
  result = snd_pcm_sw_params_set_start_threshold(pcm, parameters.get(), start);
  if (result < 0) { return result; }
  return snd_pcm_sw_params(pcm, parameters.get());
}

int configure(snd_pcm_t *pcm, const Options &configuration) {
  snd_pcm_hw_params_t *raw = nullptr;
  int result = snd_pcm_hw_params_malloc(&raw);
  const Parameters parameters(raw, &snd_pcm_hw_params_free);
  if (result < 0) { return result; }
  const auto period = configuration.rate / periods_per_second;
  // Parameter constraints must be applied in order and stop at the first failure.
  result = snd_pcm_hw_params_any(pcm, parameters.get());
  if (result < 0) { return result; }
  result = snd_pcm_hw_params_set_access(pcm, parameters.get(), SND_PCM_ACCESS_RW_INTERLEAVED);
  if (result < 0) { return result; }
  result = snd_pcm_hw_params_set_format(pcm, parameters.get(), SND_PCM_FORMAT_S24_3LE);
  if (result < 0) { return result; }
  result = snd_pcm_hw_params_set_channels(pcm, parameters.get(), channels(configuration));
  if (result < 0) { return result; }
  result = snd_pcm_hw_params_set_rate(pcm, parameters.get(), configuration.rate, 0);
  if (result < 0) { return result; }
  result = snd_pcm_hw_params_set_period_size(pcm, parameters.get(), period, 0);
  if (result < 0) { return result; }
  result = snd_pcm_hw_params_set_buffer_size(pcm, parameters.get(), period * periods);
  if (result < 0) { return result; }
  result = snd_pcm_hw_params(pcm, parameters.get());
  if (result < 0) { return result; }
  result = configure_software(pcm, configuration);
  if (result < 0) { return result; }
  return snd_pcm_prepare(pcm);
}

std::expected<void, int> wait_ready(snd_pcm_t *pcm, Clock::time_point deadline) {
  if (Clock::now() >= deadline) { return std::unexpected(-ETIMEDOUT); }
  const int result = snd_pcm_wait(pcm, poll_timeout_ms);
  if (result < 0 && result != -EINTR) { return std::unexpected(result); }
  return {};
}

struct Transfer {
  snd_pcm_uframes_t frames = 0;
  Clock::duration elapsed{};
  int error = 0;
};

int drain(snd_pcm_t *pcm, Clock::time_point deadline) {
  while (true) {
    const int result = snd_pcm_drain(pcm);
    if (result != -EAGAIN && result != -EINTR) { return result; }
    const auto ready = wait_ready(pcm, deadline);
    if (!ready) {
      // Completion can race the wait and leave the PCM already drained.
      if (snd_pcm_state(pcm) == SND_PCM_STATE_SETUP) { return 0; }
      return ready.error();
    }
  }
}

Transfer transfer(snd_pcm_t *pcm, const Options &configuration) {
  constexpr unsigned int sample_bytes = 3;
  const snd_pcm_uframes_t period = configuration.rate / periods_per_second;
  // Playback contains only zeros; captured samples are discarded.
  std::vector<std::uint8_t> buffer(period * channels(configuration) * sample_bytes);
  const auto start = Clock::now();
  const auto deadline = start + transfer_timeout;
  Transfer result;
  while (result.frames < configuration.rate) {
    if (Clock::now() >= deadline) {
      result.error = -ETIMEDOUT;
      break;
    }
    const auto pending =
        std::min(period, static_cast<snd_pcm_uframes_t>(configuration.rate) - result.frames);
    const auto count = configuration.stream == SND_PCM_STREAM_CAPTURE
                           ? snd_pcm_readi(pcm, buffer.data(), pending)
                           : snd_pcm_writei(pcm, buffer.data(), pending);
    if (count == -EINTR) { continue; }
    if (count == -EAGAIN || count == 0) {
      const auto ready = wait_ready(pcm, deadline);
      if (ready) { continue; }
      result.error = ready.error();
      break;
    }
    if (count < 0 || static_cast<snd_pcm_uframes_t>(count) > pending) {
      result.error = count < 0 ? static_cast<int>(count) : -EIO;
      break;
    }
    result.frames += static_cast<snd_pcm_uframes_t>(count);
  }
  if (result.error == 0 && configuration.stream == SND_PCM_STREAM_PLAYBACK) {
    result.error = drain(pcm, deadline);
  }
  result.elapsed = Clock::now() - start;
  return result;
}

int failure(std::string_view stage, int code) {
  std::cerr << stage << ": " << snd_strerror(code) << " (" << code << ")\n";
  return 1;
}

}

int main(int argc, char **argv) {
  const auto configuration = options(argc, argv);
  if (!configuration || !configuration->device.starts_with("hw:")) {
    std::cerr << "Usage: octa-audio-check RATE "
                 "capture|playback|prepare-capture|prepare-playback [hw:PCM]\n";
    return 2;
  }
  snd_pcm_t *raw = nullptr;
  const int opened =
      snd_pcm_open(&raw, configuration->device.c_str(), configuration->stream, SND_PCM_NONBLOCK);
  const Pcm pcm(raw);
  if (opened < 0) { return failure("open PCM", opened); }
  const int prepared = configure(pcm.get(), *configuration);
  if (prepared < 0) { return failure("prepare PCM", prepared); }
  std::cout << R"({"rate_hz":)" << configuration->rate << R"(,"transport_channels":)"
            << channels(*configuration) << R"(,"prepared":true)";
  if (configuration->prepare_only) {
    std::cout << "}\n";
    return 0;
  }
  const auto result = transfer(pcm.get(), *configuration);
  const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(result.elapsed);
  const bool timing = elapsed >= minimum_duration && elapsed <= maximum_duration;
  std::cout << R"(,"frames":)" << result.frames << R"(,"elapsed_ms":)" << elapsed.count()
            << R"(,"error":)" << result.error << R"(,"timing_ok":)" << std::boolalpha << timing
            << "}\n";
  if (result.error < 0) { return failure("stream PCM", result.error); }
  if (!timing) {
    std::cerr << "One second of audio must complete in 800..2000 ms.\n";
    return 1;
  }
  return 0;
}
