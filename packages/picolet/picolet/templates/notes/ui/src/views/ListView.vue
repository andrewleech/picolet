<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRouter, useRoute } from "vue-router";

interface NoteItem {
  slug: string;
  title: string;
  created: number;
  updated: number;
}

const router = useRouter();
const route = useRoute();

const notes = ref<NoteItem[]>([]);
const query = ref("");
const loading = ref(true);

// Client-side filter — no IPC call; full list loaded once on mount.
const filtered = computed(() =>
  query.value.trim()
    ? notes.value.filter((n) =>
        n.title.toLowerCase().includes(query.value.toLowerCase())
      )
    : notes.value
);

onMounted(async () => {
  try {
    const result = await window.picolet.invoke("list_notes");
    notes.value = result as NoteItem[];
  } catch (_e) {
    notes.value = [];
  } finally {
    loading.value = false;
  }
});

async function createNote() {
  try {
    const note = (await window.picolet.invoke("create_note", {
      title: "Untitled",
    })) as NoteItem;
    await router.push(`/edit/${note.slug}`);
  } catch (_e) {
    // noop
  }
}

function formatDate(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const months = [
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
  ];
  return `${months[d.getMonth()]} ${d.getDate()} · ${d.getFullYear()}`;
}

function isActive(slug: string): boolean {
  return route.path === `/edit/${slug}`;
}
</script>

<template>
  <div class="app-columns">
    <div class="list-pane">
      <div class="list-header">
        <p class="list-header-title">Notes</p>
        <input
          v-model="query"
          class="search-input"
          type="text"
          placeholder="Search…"
          aria-label="Search notes"
        />
      </div>
      <div class="list-actions">
        <button class="btn-new-note" @click="createNote">+ New Note</button>
      </div>
      <div class="note-list">
        <template v-if="!loading">
          <template v-if="filtered.length > 0">
            <a
              v-for="note in filtered"
              :key="note.slug"
              :href="`#/edit/${note.slug}`"
              class="note-item"
              :class="{ active: isActive(note.slug) }"
            >
              <div class="note-item-date">{{ formatDate(note.updated) }}</div>
              <div class="note-item-title">{{ note.title }}</div>
            </a>
          </template>
          <div v-else-if="query.trim()" class="note-list-empty">
            No notes match your search.
          </div>
          <div v-else class="note-list-empty">
            No notes yet. Press + to create one.
          </div>
        </template>
      </div>
    </div>
    <div class="editor-empty">
      Select a note or create one.
    </div>
  </div>
</template>
