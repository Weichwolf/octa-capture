// SPDX-License-Identifier: GPL-2.0-only
// Memory-backed ALSA fixture; never opens an audio or USB device.
#include <algorithm>
#include <alsa/asoundlib.h>
#include <alsa/pcm_external.h>
#include <alsa/pcm_ioplug.h>
#include <array>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <new>
#include <string>
#include <string_view>
#include <sys/poll.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace {

constexpr unsigned int sample_bytes = 3;
constexpr unsigned int byte_bits = 8;
constexpr std::uint32_t byte_mask = 255;
constexpr unsigned int maximum_frames = 4096;
constexpr unsigned int minimum_period_frames = 64;
constexpr unsigned int base_sample = 0x10000;
constexpr unsigned int unit_sample = 0x10000;
constexpr unsigned int channel_sample = 512;

struct Fixture {
  snd_pcm_ioplug_t io{};
  unsigned int unit{};
  int output{-1};
  int ready{-1};
  snd_pcm_uframes_t hardware{};
};

Fixture &fixture(snd_pcm_ioplug_t *io) {
  return *static_cast<Fixture *>(io->private_data);
}

std::uint32_t sample(const Fixture &value, unsigned int channel, snd_pcm_uframes_t frame) {
  const auto level = base_sample + (value.unit * unit_sample) + (channel * channel_sample) + frame;
  const auto signed_level = static_cast<std::int32_t>(level);
  return static_cast<std::uint32_t>(channel % 2 == 0 ? signed_level : -signed_level);
}

int prepare(snd_pcm_ioplug_t *io) {
  fixture(io).hardware = 0;
  if (io->stream != SND_PCM_STREAM_CAPTURE) { return 0; }
  const auto *areas = snd_pcm_ioplug_mmap_areas(io);
  if (areas == nullptr || io->buffer_size > maximum_frames) { return -EINVAL; }
  for (unsigned int channel = 0; channel < io->channels; ++channel) {
    const auto &area = areas[channel];
    if (area.first % byte_bits != 0 || area.step % byte_bits != 0) { return -EINVAL; }
    auto *bytes = static_cast<std::uint8_t *>(area.addr);
    for (snd_pcm_uframes_t frame = 0; frame < io->buffer_size; ++frame) {
      const auto bits = sample(fixture(io), channel, frame);
      const auto offset = (area.first + frame * area.step) / byte_bits;
      for (unsigned int byte = 0; byte < sample_bytes; ++byte) {
        bytes[offset + byte] = static_cast<std::uint8_t>((bits >> (byte * byte_bits)) & byte_mask);
      }
    }
  }
  return 0;
}

int start(snd_pcm_ioplug_t * /*io*/) {
  return 0;
}

int stop(snd_pcm_ioplug_t * /*io*/) {
  return 0;
}

snd_pcm_sframes_t pointer(snd_pcm_ioplug_t *io) {
  if (io->state != SND_PCM_STATE_RUNNING && io->state != SND_PCM_STATE_DRAINING) { return 0; }
  const auto application = io->appl_ptr;
  const auto available = io->stream == SND_PCM_STREAM_CAPTURE ? io->buffer_size : 0;
  auto &hardware = fixture(io).hardware;
  const auto limit = application + available;
  if (hardware > limit) { return -EPIPE; }
  // Model observable period completions rather than consuming an entire ring at once.
  hardware += std::min(limit - hardware, io->period_size);
  return static_cast<snd_pcm_sframes_t>(hardware);
}

snd_pcm_sframes_t transfer(snd_pcm_ioplug_t *io,
                           const snd_pcm_channel_area_t *areas,
                           snd_pcm_uframes_t offset,
                           snd_pcm_uframes_t size) {
  if (io->stream == SND_PCM_STREAM_CAPTURE) { return static_cast<snd_pcm_sframes_t>(size); }
  if (size > maximum_frames || offset > io->buffer_size || size > io->buffer_size - offset) {
    return -EINVAL;
  }
  std::vector<std::uint8_t> bytes(size * io->channels * sample_bytes);
  for (snd_pcm_uframes_t frame = 0; frame < size; ++frame) {
    for (unsigned int channel = 0; channel < io->channels; ++channel) {
      const auto &area = areas[channel];
      if (area.first % byte_bits != 0 || area.step % byte_bits != 0) { return -EINVAL; }
      const auto source = (area.first + (offset + frame) * area.step) / byte_bits;
      const auto destination = (frame * io->channels + channel) * sample_bytes;
      std::memcpy(bytes.data() + destination,
                  static_cast<const std::uint8_t *>(area.addr) + source,
                  sample_bytes);
    }
  }
  const auto written = write(fixture(io).output, bytes.data(), bytes.size());
  if (written < 0) { return -errno; }
  if (static_cast<std::size_t>(written) != bytes.size()) { return -EIO; }
  return static_cast<snd_pcm_sframes_t>(size);
}

int close_fixture(snd_pcm_ioplug_t *io) {
  auto *value = &fixture(io);
  if (value->output >= 0) { close(value->output); }
  if (value->ready >= 0) { close(value->ready); }
  delete value;
  return 0;
}

const auto callbacks = [] {
  snd_pcm_ioplug_callback_t table{};
  table.start = start;
  table.stop = stop;
  table.pointer = pointer;
  table.transfer = transfer;
  table.close = close_fixture;
  table.prepare = prepare;
  return table;
}();

int parameters(snd_pcm_ioplug_t *io) {
  constexpr std::array<unsigned int, 2> access = {SND_PCM_ACCESS_RW_INTERLEAVED,
                                                  SND_PCM_ACCESS_MMAP_INTERLEAVED};
  constexpr unsigned int format = SND_PCM_FORMAT_S24_3LE;
  constexpr std::array<unsigned int, 3> rates = {44100, 48000, 96000};
  const auto channels = io->stream == SND_PCM_STREAM_CAPTURE ? 12U : 10U;
  for (const int result :
       {snd_pcm_ioplug_set_param_list(io, SND_PCM_IOPLUG_HW_ACCESS, access.size(), access.data()),
        snd_pcm_ioplug_set_param_list(io, SND_PCM_IOPLUG_HW_FORMAT, 1, &format),
        snd_pcm_ioplug_set_param_list(io, SND_PCM_IOPLUG_HW_RATE, rates.size(), rates.data()),
        snd_pcm_ioplug_set_param_minmax(io, SND_PCM_IOPLUG_HW_CHANNELS, channels, channels),
        snd_pcm_ioplug_set_param_minmax(io, SND_PCM_IOPLUG_HW_PERIODS, 2, 4),
        snd_pcm_ioplug_set_param_minmax(io,
                                        SND_PCM_IOPLUG_HW_PERIOD_BYTES,
                                        channels * sample_bytes * minimum_period_frames,
                                        channels * sample_bytes * maximum_frames / 2),
        snd_pcm_ioplug_set_param_minmax(io,
                                        SND_PCM_IOPLUG_HW_BUFFER_BYTES,
                                        channels * sample_bytes * minimum_period_frames * 2,
                                        channels * sample_bytes * maximum_frames)}) {
    if (result < 0) { return result; }
  }
  return 0;
}

int settings(snd_config_t *conf, Fixture &value, std::string &directory) {
  snd_config_iterator_t iterator;
  snd_config_iterator_t next;
  snd_config_for_each(iterator, next, conf) {
    auto *item = snd_config_iterator_entry(iterator);
    const char *name = nullptr;
    if (snd_config_get_id(item, &name) < 0) { return -EINVAL; }
    const std::string_view key(name);
    if (key == "unit") {
      long unit = -1;
      if (snd_config_get_integer(item, &unit) < 0 || unit < 0 || unit > 1) { return -EINVAL; }
      value.unit = static_cast<unsigned int>(unit);
    } else if (key == "directory") {
      const char *path = nullptr;
      if (snd_config_get_string(item, &path) < 0) { return -EINVAL; }
      directory = path;
    } else if (key != "type") {
      return -EINVAL;
    }
  }
  return directory.empty() ? -EINVAL : 0;
}

} // namespace

extern "C" {
SND_PCM_PLUGIN_DEFINE_FUNC(octa_fixture) {
  static_cast<void>(root);
  auto *value = new (std::nothrow) Fixture;
  if (value == nullptr) { return -ENOMEM; }
  value->io.private_data = value;
  std::string directory;
  int result = settings(conf, *value, directory);
  if (result < 0) {
    delete value;
    return result;
  }
  const auto ready = directory + (value->unit == 0 ? "/base.ready" : "/exp.ready");
  value->ready = open(ready.c_str(), O_RDWR | O_CREAT | O_TRUNC | O_CLOEXEC, S_IRUSR | S_IWUSR);
  const auto path = directory + (value->unit == 0 ? "/base.raw" : "/exp.raw");
  if (stream == SND_PCM_STREAM_PLAYBACK) {
    value->output = open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, S_IRUSR | S_IWUSR);
  }
  if (value->ready < 0 || (stream == SND_PCM_STREAM_PLAYBACK && value->output < 0)) {
    result = -errno;
    close_fixture(&value->io);
    return result;
  }
  value->io.version = SND_PCM_IOPLUG_VERSION;
  value->io.name = "OCTA offline aggregate fixture";
  value->io.flags = SND_PCM_IOPLUG_FLAG_BOUNDARY_WA | SND_PCM_IOPLUG_FLAG_MONOTONIC;
  value->io.poll_fd = value->ready;
  value->io.poll_events = stream == SND_PCM_STREAM_PLAYBACK ? POLLOUT : POLLIN;
  value->io.mmap_rw = 1;
  value->io.callback = &callbacks;
  value->io.private_data = value;
  result = snd_pcm_ioplug_create(&value->io, name, stream, mode);
  if (result < 0) {
    close_fixture(&value->io);
    return result;
  }
  result = parameters(&value->io);
  if (result < 0) {
    snd_pcm_ioplug_delete(&value->io);
    return result;
  }
  *pcmp = value->io.pcm;
  return 0;
}
// The convenience wrapper adds a second semicolon, rejected by strict C++ flags.
SND_DLSYM_BUILD_VERSION(SND_PCM_PLUGIN_ENTRY(octa_fixture), SND_PCM_DLSYM_VERSION)
}
