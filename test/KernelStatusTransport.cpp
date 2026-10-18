// SPDX-License-Identifier: GPL-2.0-only
#include "KernelStatusTransport.hpp"

#include "StatusTypes.h"
extern "C" {
#include "octa.h"
}

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <span>
#include <string_view>

namespace {
octa::kernel_status_test::Transport context;
constexpr unsigned int status_interface = 3;
constexpr unsigned int status_alternate = 1;
constexpr unsigned int status_endpoint = 0x82;
constexpr int timeout_ms = 250;
constexpr unsigned int packet_mask = 0x7ff;
constexpr std::size_t maximum_allocation = 4096;

void *allocate(std::size_t size, unsigned int flags, bool zero) {
  ++context.allocations_attempted;
  if (context.fail_allocation == context.allocations_attempted) { return nullptr; }
  if (size == 0 || size > maximum_allocation || flags != GFP_KERNEL) {
    context.invalid_request = true;
    return nullptr;
  }
  for (std::size_t index = 0; index < context.allocations.size(); ++index) {
    if (context.allocations[index] != nullptr) { continue; }
    void *memory = zero ? std::calloc(1, size) : std::malloc(size);
    context.allocations[index] = memory;
    context.allocation_sizes[index] = size;
    return memory;
  }
  context.invalid_request = true;
  return nullptr;
}

bool dma_buffer(const void *pointer) {
  for (std::size_t index = 0; index < context.allocations.size(); ++index) {
    if (context.allocations[index] == pointer &&
        context.allocation_sizes[index] == octa::kernel_status_test::packet_size) {
      return true;
    }
  }
  return false;
}
}

namespace octa::kernel_status_test {
Transport &transport() {
  return context;
}

void cleanup() {
  if (context.control.private_data != nullptr && context.control.private_free != nullptr) {
    context.control.private_free(&context.control);
    context.control.private_data = nullptr;
  }
  for (auto *&pointer : context.allocations) {
    std::free(pointer);
    pointer = nullptr;
  }
}

void reset() {
  cleanup();
  context = Transport{};
  context.chip = {.dev = &context.device, .usb_id = USB_ID(0x0582, 0x0120), .card = &context.card};
  context.active.endpoint = &context.endpoint;
  context.interface.cur_altsetting = &context.inactive;
  context.replies[1].packet = {1, 0, 0, 0x80, rate_middle, 0, 0, 0};
}

int get(std::array<std::uint8_t, output_size> &output) {
  snd_ctl_elem_value value{};
  std::ranges::fill(value.value.bytes.data, sentinel);
  if (context.description.get == nullptr || context.control.private_data == nullptr) {
    context.invalid_request = true;
    return -EINVAL;
  }
  const int result = context.description.get(&context.control, &value);
  std::ranges::copy(value.value.bytes.data, output.begin());
  return result;
}

bool balanced() {
  const auto allocations = std::ranges::count_if(
      context.allocations, [](const auto *pointer) { return pointer != nullptr; });
  const int owned = context.control.private_data == nullptr ? 0 : 1;
  const bool tracked = context.control.private_data == nullptr ||
                       std::ranges::find(context.allocations, context.control.private_data) !=
                           context.allocations.end();
  return !context.invalid_request && context.mutex_depth == 0 && context.shutdown_depth == 0 &&
         allocations == owned && tracked;
}
}

extern "C" void mutex_init(struct mutex *lock) {
  lock->held = false;
}

extern "C" int mutex_lock_interruptible(struct mutex *lock) {
  if (context.lock_error != 0) { return context.lock_error; }
  if (lock->held || context.mutex_depth != 0) { context.invalid_request = true; }
  lock->held = true;
  ++context.mutex_depth;
  return 0;
}

extern "C" void mutex_unlock(struct mutex *lock) {
  if (!lock->held || context.mutex_depth != 1) {
    context.invalid_request = true;
    return;
  }
  lock->held = false;
  --context.mutex_depth;
}

extern "C" void *kmalloc(std::size_t size, unsigned int flags) {
  return allocate(size, flags, false);
}

extern "C" void *kzalloc(std::size_t size, unsigned int flags) {
  return allocate(size, flags, true);
}

extern "C" void kfree(const void *pointer) {
  if (pointer == nullptr) { return; }
  for (auto *&allocation : context.allocations) {
    if (allocation != pointer) { continue; }
    std::free(allocation);
    allocation = nullptr;
    ++context.frees;
    return;
  }
  context.invalid_request = true;
}

extern "C" struct usb_interface *usb_ifnum_to_if(struct usb_device *device, unsigned int index) {
  if (device != &context.device || index != status_interface || context.shutdown_depth != 1) {
    context.invalid_request = true;
  }
  return context.missing_interface ? nullptr : &context.interface;
}

extern "C" struct usb_host_interface *usb_altnum_to_altsetting(struct usb_interface *interface,
                                                               unsigned int index) {
  if (interface != &context.interface || index != status_alternate) {
    context.invalid_request = true;
  }
  return context.missing_alternate ? nullptr : &context.active;
}

extern "C" int usb_endpoint_is_int_in(const struct usb_endpoint_descriptor *endpoint) {
  return static_cast<int>((endpoint->bEndpointAddress & 0x80) != 0 &&
                          (endpoint->bmAttributes & 3) == 3);
}

extern "C" unsigned int usb_endpoint_maxp(const struct usb_endpoint_descriptor *endpoint) {
  return endpoint->wMaxPacketSize & packet_mask;
}

extern "C" int usb_set_interface(struct usb_device *device, int index, int alternate) {
  ++context.set_calls;
  if (device != &context.device || index != status_interface || alternate != status_alternate ||
      context.shutdown_depth != 1 || context.mutex_depth != 1) {
    context.invalid_request = true;
  }
  if (context.alternate_error == 0) { context.interface.cur_altsetting = &context.active; }
  return context.alternate_error;
}

extern "C" unsigned int usb_rcvintpipe(struct usb_device *device, unsigned int endpoint) {
  if (device != &context.device || endpoint != status_endpoint) { context.invalid_request = true; }
  return status_endpoint;
}

extern "C" int octa_test_interrupt_msg(const struct octa_test_interrupt *request) {
  if (context.calls >= context.replies.size() || request->device != &context.device ||
      request->pipe != status_endpoint ||
      request->length != octa::kernel_status_test::packet_size || request->timeout != timeout_ms ||
      context.mutex_depth != 1 || context.shutdown_depth != 1 || !dma_buffer(request->data)) {
    context.invalid_request = true;
    return -EPROTO;
  }
  const auto &reply = context.replies[context.calls++];
  *request->actual = reply.actual;
  if (reply.result == 0 && reply.actual > 0) {
    const auto count = std::min(static_cast<std::size_t>(reply.actual), reply.packet.size());
    std::ranges::copy(std::span(reply.packet).first(count),
                      static_cast<std::uint8_t *>(request->data));
  }
  return reply.result;
}

extern "C" int snd_usb_lock_shutdown(struct snd_usb_audio *chip) {
  if (chip != &context.chip || context.mutex_depth != 1) { context.invalid_request = true; }
  if (context.shutdown_error != 0) { return context.shutdown_error; }
  ++context.shutdown_depth;
  return 0;
}

extern "C" void snd_usb_unlock_shutdown(struct snd_usb_audio *chip) {
  if (chip != &context.chip || context.shutdown_depth != 1) {
    context.invalid_request = true;
    return;
  }
  --context.shutdown_depth;
}

extern "C" void *snd_kcontrol_chip(struct snd_kcontrol *control) {
  if (control != &context.control) { context.invalid_request = true; }
  return control->private_data;
}

extern "C" struct snd_kcontrol *snd_ctl_new1(const struct snd_kcontrol_new *description,
                                             void *data) {
  if (context.fail_control) { return nullptr; }
  if (description->iface != SNDRV_CTL_ELEM_IFACE_CARD ||
      std::string_view(description->name) != "OCTA-CAPTURE Status" ||
      description->access != (SNDRV_CTL_ELEM_ACCESS_READ | SNDRV_CTL_ELEM_ACCESS_VOLATILE)) {
    context.invalid_request = true;
  }
  context.description = *description;
  context.control.private_data = data;
  return &context.control;
}

extern "C" int snd_ctl_add(struct snd_card *card, struct snd_kcontrol *control) {
  if (card != &context.card || control != &context.control || control->private_free == nullptr) {
    context.invalid_request = true;
    return -EINVAL;
  }
  if (context.add_error != 0) {
    control->private_free(control);
    control->private_data = nullptr;
  }
  return context.add_error;
}

extern "C" int snd_usb_octa_capture_add_pcm_controls(struct snd_usb_audio *chip) {
  if (chip != &context.chip) { context.invalid_request = true; }
  ++context.pcm_calls;
  return context.pcm_error;
}
