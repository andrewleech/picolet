// asset-load-e2e: reports background colour and execution marker to Python.
(function() {
  var bg = window.getComputedStyle(document.body).backgroundColor;
  window.webkit.messageHandlers.picolet.postMessage(
    JSON.stringify({ event: "asset-check", data: { bg: bg, from: "app.js" } })
  );
})();
