<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import { marked } from "marked";

interface NoteData {
  slug: string;
  title: string;
  body: string;
  created: number;
  updated: number;
}

const route = useRoute();
const router = useRouter();

const slug = computed(() => route.params.slug as string);
const title = ref("Untitled");
const body = ref("");
const savedBody = ref("");
const savedTitle = ref("");
const showPreview = ref(false);
const loading = ref(true);
const titleEl = ref<HTMLElement | null>(null);

// Unsaved state: body or title differs from last-saved value.
const isUnsaved = computed(
  () => body.value !== savedBody.value || title.value !== savedTitle.value
);

// Rendered markdown for preview mode.
const renderedBody = computed(() => marked.parse(body.value) as string);

onMounted(async () => {
  try {
    const note = (await window.picolet.invoke("load_note", {
      slug: slug.value,
    })) as NoteData;
    title.value = note.title;
    body.value = note.body;
    savedBody.value = note.body;
    savedTitle.value = note.title;
    // Set contenteditable content after DOM is ready.
    await nextTick();
    if (titleEl.value) {
      titleEl.value.innerText = note.title;
    }
  } catch (_e) {
    title.value = "Untitled";
    body.value = "";
    savedBody.value = "";
    savedTitle.value = "Untitled";
    await nextTick();
    if (titleEl.value) {
      titleEl.value.innerText = "Untitled";
    }
  } finally {
    loading.value = false;
  }

  document.addEventListener("keydown", onKeydown);
});

onUnmounted(() => {
  document.removeEventListener("keydown", onKeydown);
});

function onKeydown(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key === "s") {
    e.preventDefault();
    saveNote();
  }
}

async function saveNote() {
  if (!isUnsaved.value) return;
  try {
    if (title.value !== savedTitle.value) {
      // rename_note updates front-matter title only; body unchanged.
      await window.picolet.invoke("rename_note", {
        slug: slug.value,
        title: title.value,
      });
      savedTitle.value = title.value;
    }
    await window.picolet.invoke("save_note", {
      slug: slug.value,
      body: body.value,
    });
    savedBody.value = body.value;
  } catch (_e) {
    // noop — unsaved dot remains visible
  }
}

async function deleteNote() {
  try {
    await window.picolet.invoke("delete_note", { slug: slug.value });
  } catch (_e) {
    // noop
  }
  await router.push("/");
}

function onTitleInput(e: Event) {
  const el = e.target as HTMLElement;
  // Sanitise: collapse newlines, strip HTML tags (browser may insert them).
  const raw = el.innerText ?? "";
  title.value = raw.replace(/\n/g, " ").trim() || "Untitled";
}
</script>

<template>
  <div class="app-columns">
    <!-- List pane (visible on wide screens as minimal nav) -->
    <div class="list-pane">
      <div class="list-header">
        <p class="list-header-title">Notes</p>
      </div>
      <div class="list-actions">
        <a href="#/" class="btn-new-note back-to-list">← All Notes</a>
      </div>
    </div>

    <!-- Editor pane -->
    <div class="editor-pane" v-if="!loading">
      <!-- Unsaved indicator: 8px circle in --mark (#c4392b).
           The ONLY place that colour appears in the app. -->
      <span v-if="isUnsaved" class="unsaved-dot" aria-label="Unsaved changes" />

      <!-- Back navigation -->
      <nav class="editor-nav">
        <a href="#/" class="back-to-list">← Notes</a>
      </nav>

      <!-- Title: contenteditable h1 — feels like writing on a page. -->
      <h1
        ref="titleEl"
        class="editor-title"
        contenteditable="true"
        data-placeholder="Untitled"
        @input="onTitleInput"
        spellcheck="false"
      />

      <!-- Toolbar: preview toggle + delete -->
      <div class="editor-toolbar">
        <button
          class="btn-toggle-preview"
          @click="showPreview = !showPreview"
        >{{ showPreview ? "edit" : "preview" }}</button>
        <button class="btn-delete-note" @click="deleteNote">delete</button>
      </div>

      <!-- Editor textarea for writing -->
      <textarea
        v-if="!showPreview"
        v-model="body"
        class="note-body"
        placeholder="Start writing…"
        spellcheck="false"
      />

      <!-- Preview: rendered markdown via marked -->
      <div
        v-else
        class="preview"
        v-html="renderedBody"
      />
    </div>
  </div>
</template>
