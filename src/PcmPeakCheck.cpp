// SPDX-License-Identifier: GPL-2.0-only
// Quiet real-URB test: channel tags are at most ten S24 LSBs (-118 dBFS).
#include "octa/PcmMeterControl.hpp"
#include "octa/PcmMeters.hpp"
#include "octa/StatusControl.hpp"

#include <alsa/asoundlib.h>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <memory>
#include <span>
#include <vector>

namespace {

constexpr std::size_t sample_bytes = 3;
constexpr unsigned int quarter_second = 4;
constexpr unsigned int latency_us = 40000;
constexpr int wait_ms = 30;
constexpr auto transfer_deadline = std::chrono::seconds(4);
constexpr std::uint32_t peak_shift = 256;
constexpr std::uint8_t negative_byte = 255;

struct ClosePcm {
  void operator()(snd_pcm_t *pcm) const noexcept {
    snd_pcm_drop(pcm);
    snd_pcm_close(pcm);
  }
};

using Pcm = std::unique_ptr<snd_pcm_t, ClosePcm>;

int transfer(snd_pcm_t *pcm, std::span<const std::uint8_t> samples, unsigned int channels) {
  const std::size_t stride = channels * sample_bytes;
  const std::size_t frames = samples.size() / stride;
  std::size_t offset = 0;
  const auto deadline = std::chrono::steady_clock::now() + transfer_deadline;
  while (offset < frames) {
    if (std::chrono::steady_clock::now() >= deadline) { return -ETIMEDOUT; }
    const auto count = snd_pcm_writei(pcm, samples.data() + (offset * stride), frames - offset);
    if (count == -EAGAIN || count == 0) {
      const int waited = snd_pcm_wait(pcm, wait_ms);
      if (waited < 0) { return waited; }
    } else if (count < 0) {
      return static_cast<int>(count);
    } else {
      offset += static_cast<std::size_t>(count);
    }
  }
  return 0;
}

bool verify(octa::PcmMeterControl &meter, unsigned int channels, unsigned int rate, bool positive) {
  const auto frame = meter.read();
  if (!frame || !frame->playback.fresh || frame->playback.rate != rate ||
      frame->playback.channels != channels) {
    std::cerr << "Missing fresh playback peaks.\n";
    return false;
  }
  for (std::size_t channel = 0; channel < channels; ++channel) {
    const auto expected = positive ? (channel + 1) * peak_shift : 0;
    if (frame->playback.peaks[channel] != expected) {
      std::cerr << "Channel " << channel + 1 << ": peak " << frame->playback.peaks[channel]
                << ", expected " << expected << '\n';
      return false;
    }
  }
  return true;
}

int check(snd_pcm_t *pcm, octa::PcmMeterControl &meter, unsigned int channels, unsigned int rate) {
  std::vector<std::uint8_t> samples(static_cast<std::size_t>(rate / quarter_second) * channels *
                                    sample_bytes);
  const auto initial = meter.read();
  if (!initial) { return -EIO; }
  // One tiny positive frame, then >200 ms of zeros before reading the peaks.
  for (unsigned int channel = 0; channel < channels; ++channel) {
    samples[channel * sample_bytes] = static_cast<std::uint8_t>(channel + 1);
  }
  int result = transfer(pcm, samples, channels);
  if (result < 0) { return result; }
  if (!verify(meter, channels, rate, true)) { return -EBADMSG; }
  for (unsigned int channel = 0; channel < channels; ++channel) {
    for (unsigned int byte = 0; byte < sample_bytes; ++byte) {
      samples[(channel * sample_bytes) + byte] = negative_byte;
    }
  }
  result = transfer(pcm, samples, channels);
  if (result < 0) { return result; }
  return verify(meter, channels, rate, false) ? 0 : -EBADMSG;
}

}

int main(int argc, char ** /*argv*/) {
  if (argc != 1) {
    std::cerr << "Usage: octa-pcm-peak-check (exclusive meter access; very quiet channel tags)\n";
    return 2;
  }
  const auto status = octa::read_device_status("hw:CARD=OCTACAPTURE");
  if (!status) { return 1; }
  constexpr unsigned int high_rate = 192000;
  const unsigned int channels = status->sample_rate_hz == high_rate ? 4 : 10;
  auto meter = octa::PcmMeterControl::open("hw:CARD=OCTACAPTURE");
  if (!meter) { return 1; }
  snd_pcm_t *raw = nullptr;
  int result =
      snd_pcm_open(&raw, "hw:CARD=OCTACAPTURE,DEV=0", SND_PCM_STREAM_PLAYBACK, SND_PCM_NONBLOCK);
  const Pcm pcm(raw);
  if (result < 0) { return 1; }
  result = snd_pcm_set_params(pcm.get(),
                              SND_PCM_FORMAT_S24_3LE,
                              SND_PCM_ACCESS_RW_INTERLEAVED,
                              channels,
                              status->sample_rate_hz,
                              0,
                              latency_us);
  if (result < 0 || !meter->enable(true)) { return 1; }
  result = check(pcm.get(), *meter, channels, status->sample_rate_hz);
  const auto stopped = meter->enable(false);
  if (result < 0 || !stopped) {
    std::cerr << "PCM peak check failed: " << result << '\n';
    return 1;
  }
  std::cout << "PASS " << status->sample_rate_hz << " Hz: " << channels
            << " exact channel tags retained across >200 ms of zeros; read-clear and negative "
               "rejection\n";
  return 0;
}
