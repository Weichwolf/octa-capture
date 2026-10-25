// SPDX-License-Identifier: GPL-2.0-only
// Real ALSA multi/route client for the isolated synthetic PCM fixture.
#include <algorithm>
#include <alsa/asoundlib.h>
#include <array>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>
#include <string_view>
#include <sys/poll.h>
#include <system_error>
#include <unistd.h>
#include <vector>

namespace {
constexpr std::size_t total_frames = 4096;
constexpr unsigned int period_frames = 256;
constexpr snd_pcm_uframes_t buffer_frames = 1024;
constexpr unsigned int sample_bytes = 3;
constexpr unsigned int byte_bits = 8;
constexpr std::uint32_t byte_mask = 255;
constexpr int sample_bias = 40960;
constexpr unsigned int playback_channels = 20;
constexpr unsigned int capture_channels = 24;
constexpr std::array<unsigned int, 3> rates = {44100, 48000, 96000};
constexpr std::size_t path_capacity = 4096;

void require(bool condition, std::string_view message) {
  if (!condition) {
    std::cerr << "FAIL " << message << '\n';
    std::_Exit(1);
  }
}

void checked(int result) {
  if (result < 0) {
    std::cerr << "ALSA: " << snd_strerror(result) << '\n';
    std::_Exit(1);
  }
}

void check_master(snd_pcm_t *pcm) {
  require(snd_pcm_poll_descriptors_count(pcm) == 1, "One master poll descriptor");
  std::array<pollfd, 1> descriptors{};
  require(snd_pcm_poll_descriptors(pcm, descriptors.data(), descriptors.size()) == 1,
          "Aggregate exposes its timing master's poll descriptor");
  const auto name = "/proc/self/fd/" + std::to_string(descriptors[0].fd);
  std::array<char, path_capacity> path{};
  const auto size = readlink(name.c_str(), path.data(), path.size());
  require(size > 0 && static_cast<std::size_t>(size) < path.size(), "Master descriptor target");
  require(std::string_view(path.data(), static_cast<std::size_t>(size)).ends_with("/exp.ready"),
          "The EXP device is the ALSA multi timing master");
}

struct Parameters {
  unsigned int channels;
  unsigned int rate;
};

void configure(snd_pcm_t *pcm, Parameters settings) {
  snd_pcm_hw_params_t *raw = nullptr;
  checked(snd_pcm_hw_params_malloc(&raw));
  const std::unique_ptr<snd_pcm_hw_params_t, decltype(&snd_pcm_hw_params_free)> parameters(
      raw, &snd_pcm_hw_params_free);
  checked(snd_pcm_hw_params_any(pcm, raw));
  checked(snd_pcm_hw_params_set_access(pcm, raw, SND_PCM_ACCESS_RW_INTERLEAVED));
  checked(snd_pcm_hw_params_set_format(pcm, raw, SND_PCM_FORMAT_S24_3LE));
  checked(snd_pcm_hw_params_set_channels(pcm, raw, settings.channels));
  checked(snd_pcm_hw_params_set_rate(pcm, raw, settings.rate, 0));
  checked(snd_pcm_hw_params_set_period_size(pcm, raw, period_frames, 0));
  checked(snd_pcm_hw_params_set_buffer_size(pcm, raw, buffer_frames));
  checked(snd_pcm_hw_params(pcm, raw));
  snd_pcm_uframes_t actual = 0;
  checked(snd_pcm_hw_params_get_buffer_size(raw, &actual));
  require(actual == buffer_frames, "Expected fixture buffer size");
  snd_pcm_sw_params_t *software = nullptr;
  checked(snd_pcm_sw_params_malloc(&software));
  const std::unique_ptr<snd_pcm_sw_params_t, decltype(&snd_pcm_sw_params_free)> sw_parameters(
      software, &snd_pcm_sw_params_free);
  checked(snd_pcm_sw_params_current(pcm, software));
  const auto threshold = snd_pcm_stream(pcm) == SND_PCM_STREAM_PLAYBACK ? buffer_frames : 1;
  checked(snd_pcm_sw_params_set_start_threshold(pcm, software, threshold));
  checked(snd_pcm_sw_params_set_avail_min(pcm, software, period_frames));
  checked(snd_pcm_sw_params(pcm, software));
}

std::vector<std::uint8_t> playback(unsigned int channels) {
  std::vector<std::uint8_t> result(total_frames * channels * sample_bytes);
  for (std::size_t frame = 0; frame < total_frames; ++frame) {
    for (unsigned int channel = 0; channel < channels; ++channel) {
      const auto value = static_cast<int>((frame * channels) + channel) - sample_bias;
      const auto bits = static_cast<std::uint32_t>(value);
      const auto offset = ((frame * channels) + channel) * sample_bytes;
      for (unsigned int byte = 0; byte < sample_bytes; ++byte) {
        result[offset + byte] = static_cast<std::uint8_t>((bits >> (byte * byte_bits)) & byte_mask);
      }
    }
  }
  return result;
}

void transfer(snd_pcm_t *pcm, bool capture, const char *path) {
  const unsigned int channels = capture ? capture_channels : playback_channels;
  auto data = capture ? std::vector<std::uint8_t>(total_frames * channels * sample_bytes)
                      : playback(channels);
  for (std::size_t frame = 0; frame < total_frames; frame += period_frames) {
    auto *block = data.data() + (frame * channels * sample_bytes);
    const auto result = capture ? snd_pcm_readi(pcm, block, period_frames)
                                : snd_pcm_writei(pcm, block, period_frames);
    require(result == period_frames, "Complete aggregate period transfer");
  }
  if (capture) {
    checked(snd_pcm_drop(pcm));
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    output.write(reinterpret_cast<const char *>(data.data()),
                 static_cast<std::streamsize>(data.size()));
    output.close();
    require(static_cast<bool>(output), "Write synthetic aggregate capture");
  } else {
    checked(snd_pcm_drain(pcm));
  }
}
} // namespace

int main(int argc, char **argv) {
  require(argc == 4, "Expected rate, playback/capture and capture output path");
  // Fresh /dev in the test namespace excludes hardware even if configuration is broken.
  require(access("/dev/snd", F_OK) != 0 && access("/dev/bus/usb", F_OK) != 0,
          "An isolated /dev without sound/USB devices is required");
  unsigned int rate = 0;
  const std::string_view rate_text(argv[1]);
  const auto parsed = std::from_chars(rate_text.data(), rate_text.data() + rate_text.size(), rate);
  require(parsed.ec == std::errc{} && parsed.ptr == rate_text.data() + rate_text.size() &&
              std::ranges::find(rates, rate) != rates.end(),
          "Supported aggregate rate");
  const std::string_view direction(argv[2]);
  require(direction == "capture" || direction == "playback", "Expected transfer direction");
  const bool capture = direction == "capture";
  snd_pcm_t *raw = nullptr;
  checked(snd_pcm_open(
      &raw, "octa_expand", capture ? SND_PCM_STREAM_CAPTURE : SND_PCM_STREAM_PLAYBACK, 0));
  const std::unique_ptr<snd_pcm_t, decltype(&snd_pcm_close)> pcm(raw, &snd_pcm_close);
  configure(raw, {.channels = capture ? capture_channels : playback_channels, .rate = rate});
  check_master(raw);
  transfer(raw, capture, argv[3]);
  std::cout << "PASS aggregate " << direction << ' ' << rate << " Hz, " << total_frames
            << " frames\n";
}
