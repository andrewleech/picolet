<script setup lang="ts">
import { inject } from "vue";
import DeviceList from "../components/DeviceList.vue";
import DeviceDetail from "../components/DeviceDetail.vue";
import type { DfuDevice } from "../components/DeviceList.vue";
import type { Ref } from "vue";

// Receive selectedDevice from App via provide/inject
const selectedDevice = inject<Ref<DfuDevice | undefined>>("selectedDevice")!;
const setSelectedDevice = inject<(d: DfuDevice) => void>("setSelectedDevice")!;
</script>

<template>
  <div class="home-view reveal-main">
    <DeviceList
      :selected-id="selectedDevice?.id"
      @select-device="setSelectedDevice"
    />
    <DeviceDetail :device="selectedDevice" />
  </div>
</template>

<style scoped>
.home-view {
  display: flex;
  flex: 1;
  min-height: 0;
}
</style>
