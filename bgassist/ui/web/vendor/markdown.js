/* A small, dependency-free Markdown renderer.
 *
 * A bundled app must work offline and the page's CSP forbids remote origins,
 * so nothing here is loaded from a CDN. Answers are short spoken-style text,
 * so this covers what actually turns up — paragraphs, emphasis, inline code,
 * fenced code, lists, links — and escapes everything else rather than trying
 * to be a complete CommonMark implementation.
 */
(function (global) {
  "use strict";

  function escapeHtml(text) {
    return text.replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function inline(text) {
    return escapeHtml(text)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|\W)\*([^*]+)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
               '<a href="$2" rel="noreferrer">$1</a>');
  }

  function render(source) {
    var lines = String(source || "").split(/\r?\n/);
    var out = [];
    var paragraph = [];
    var list = null;
    var code = null;

    function flushParagraph() {
      if (paragraph.length) {
        out.push("<p>" + inline(paragraph.join(" ")) + "</p>");
        paragraph = [];
      }
    }
    function flushList() {
      if (list) {
        out.push("<" + list.tag + ">" + list.items.map(function (i) {
          return "<li>" + inline(i) + "</li>";
        }).join("") + "</" + list.tag + ">");
        list = null;
      }
    }

    lines.forEach(function (line) {
      var fence = line.match(/^```(\w*)\s*$/);
      if (fence) {
        if (code === null) { flushParagraph(); flushList(); code = []; }
        else { out.push("<pre><code>" + escapeHtml(code.join("\n")) + "</code></pre>"); code = null; }
        return;
      }
      if (code !== null) { code.push(line); return; }

      var heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        flushParagraph(); flushList();
        var level = heading[1].length + 1;
        out.push("<h" + level + ">" + inline(heading[2]) + "</h" + level + ">");
        return;
      }
      var bullet = line.match(/^\s*[-*]\s+(.*)$/);
      // One or two digits only. Spoken answers open with a year far more
      // often than with a numbered list — "1066. Harold was killed at
      // Hastings" is a sentence, and rendering it as list item 1066 is
      // both wrong and comic.
      var number = line.match(/^\s*\d{1,2}[.)]\s+(.*)$/);
      if (bullet || number) {
        flushParagraph();
        var tag = bullet ? "ul" : "ol";
        if (!list || list.tag !== tag) { flushList(); list = { tag: tag, items: [] }; }
        list.items.push((bullet || number)[1]);
        return;
      }
      if (!line.trim()) { flushParagraph(); flushList(); return; }
      flushList();
      paragraph.push(line.trim());
    });

    if (code !== null) out.push("<pre><code>" + escapeHtml(code.join("\n")) + "</code></pre>");
    flushParagraph();
    flushList();
    return out.join("\n");
  }

  global.markdown = { render: render, escape: escapeHtml };
})(window);
