<script setup lang="ts">
// AboutView — keyboard shortcuts and storage info.
</script>

<template>
  <div class="about-view">
    <h1>Notes</h1>
    <p>
      A plain-text markdown notes app. Notes are stored as
      <code>.md</code> files with YAML front matter on your local filesystem.
    </p>
    <p>
      <strong>Keyboard shortcuts</strong><br />
      <kbd>Ctrl+S</kbd> / <kbd>Cmd+S</kbd> — save the current note
    </p>
    <p>
      Storage location:<br />
      Linux: <code>~/.config/notes/</code><br />
      Windows: <code>%APPDATA%\notes\</code>
    </p>
    <p>
      <a href="#/">← Back</a>
    </p>
  </div>
</template>
