// A small markdown renderer for answers.
//
// Answers quote log lines verbatim, so the text is untrusted: everything is
// escaped first and only the handful of tags below are ever produced. That is
// the whole reason this exists instead of a library — the escape step is not
// optional here.

const escapeHtml = (text) =>
  String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

const inline = (text) =>
  escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

/** Markdown → HTML string. Callers assign it to innerHTML. */
export function renderMarkdown(source) {
  const lines = String(source ?? "").split("\n");
  const html = [];

  let listOpen = false;
  let fenceOpen = false;
  let fence = [];

  const closeList = () => {
    if (listOpen) {
      html.push("</ul>");
      listOpen = false;
    }
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (fenceOpen) {
        html.push(`<pre><code>${escapeHtml(fence.join("\n"))}</code></pre>`);
        fence = [];
        fenceOpen = false;
      } else {
        closeList();
        fenceOpen = true;
      }
      continue;
    }

    if (fenceOpen) {
      fence.push(line);
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = Math.min(6, heading[1].length + 2); // #  → h3, so the card title stays the h2
      html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }

    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) {
      if (!listOpen) {
        html.push("<ul>");
        listOpen = true;
      }
      html.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }

    if (line.trim() === "") {
      closeList();
      continue;
    }

    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      closeList();
      html.push(`<blockquote>${inline(quote[1])}</blockquote>`);
      continue;
    }

    closeList();
    html.push(`<p>${inline(line)}</p>`);
  }

  if (fenceOpen) html.push(`<pre><code>${escapeHtml(fence.join("\n"))}</code></pre>`);
  closeList();

  return html.join("\n");
}

/** Fill a node with rendered markdown. */
export function setMarkdown(node, source) {
  node.innerHTML = renderMarkdown(source);
  return node;
}
