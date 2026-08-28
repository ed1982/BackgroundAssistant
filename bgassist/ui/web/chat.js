/* The chat window. Talks to Python over QWebChannel; no network, no CDN. */
(function () {
  "use strict";

  var backend = null;
  var state = { conversationId: null, streaming: "", busy: false,
                triggerWord: "" };

  // A small inline trash, rather than an emoji: it has to sit quietly in a
  // list and only speak up on hover.
  var TRASH_SVG =
    '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">' +
    '<path fill="currentColor" d="M6 2h4a1 1 0 0 1 1 1v1h3v1.5H2V4h3V3a1 1 0 0 1 1-1zm.5 2h3V3.5h-3V4zM3.5 6h9l-.6 7.1a1.5 1.5 0 0 1-1.5 1.4H5.6a1.5 1.5 0 0 1-1.5-1.4L3.5 6zm2.2 1.5.4 5.5h.9l-.4-5.5h-.9zm3.7 0-.4 5.5h.9l.4-5.5h-.9z"/>' +
    '</svg>';

  var $ = function (id) { return document.getElementById(id); };

  function connect(callback) {
    new QWebChannel(qt.webChannelTransport, function (channel) {
      backend = channel.objects.backend;
      callback();
    });
  }

  // -- rendering ---------------------------------------------------------

  function messageNode(message) {
    var node = document.createElement("div");
    node.className = "msg " + message.role + (message.superseded ? " superseded" : "");

    var who = document.createElement("div");
    who.className = "who";
    who.textContent = message.role === "user" ? "You" : "Assistant";
    if (message.source === "typed") who.textContent += " · typed";
    if (message.superseded) {
      who.innerHTML += '<span class="badge">cancelled</span>';
    } else if (message.interrupted) {
      who.innerHTML += '<span class="badge">interrupted</span>';
    }
    node.appendChild(who);

    var body = document.createElement("div");
    body.className = "body";
    body.innerHTML = markdown.render(message.text);
    node.appendChild(body);

    // What it was about to say, dimmed behind a disclosure (§5.4.1).
    if (message.unspoken && message.unspoken.trim()) {
      node.appendChild(disclosure("Show what it was about to say",
                                  message.unspoken, "unspoken"));
    }
    // Exactly which ambient context left the machine, so the privacy model is
    // visible rather than a claim (§7).
    if (message.role === "user" && message.context && message.context.trim()) {
      node.appendChild(disclosure("Show the context that was sent", message.context));
    }

    if (message.role === "assistant") {
      var actions = document.createElement("div");
      actions.className = "actions";
      actions.appendChild(button("Copy", function () {
        navigator.clipboard.writeText(message.text);
      }));
      actions.appendChild(button("Speak again", function () {
        backend.speakAgain(state.conversationId, message.id, function () {});
      }));
      node.appendChild(actions);
    }
    return node;
  }

  function disclosure(label, content, extraClass) {
    var details = document.createElement("details");
    details.className = "disclosure";
    var summary = document.createElement("summary");
    summary.textContent = label;
    details.appendChild(summary);
    var inner = document.createElement("div");
    inner.className = "content " + (extraClass || "");
    inner.textContent = content;
    details.appendChild(inner);
    return details;
  }

  function button(label, onClick) {
    var element = document.createElement("button");
    element.textContent = label;
    element.addEventListener("click", onClick);
    return element;
  }

  function renderMessages(messages) {
    var list = $("messages");
    list.innerHTML = "";
    if (!messages.length) {
      var word = state.triggerWord || "the trigger word";
      var empty = document.createElement("div");
      empty.className = "empty";
      var headline = document.createElement("p");
      headline.className = "empty-headline";
      headline.textContent = "It already heard the question.";
      var body = document.createElement("p");
      body.textContent = "It hears the last couple of minutes of the room, " +
        "held in memory and nowhere else. Say “" + word + "” on its own and " +
        "it answers whatever you were just talking about — you do not have " +
        "to ask again.";
      var also = document.createElement("p");
      also.className = "faint";
      also.textContent = "Or ask it directly, in any phrasing: “" + word +
        ", what year was that?” works, and so does “what year was that, " +
        word + "?”";
      empty.appendChild(headline);
      empty.appendChild(body);
      empty.appendChild(also);
      list.appendChild(empty);
      return;
    }
    messages.forEach(function (message) { list.appendChild(messageNode(message)); });
    list.scrollTop = list.scrollHeight;
  }

  function renderConversations(items) {
    var list = $("conversations");
    list.innerHTML = "";
    items.forEach(function (item) {
      var li = document.createElement("li");
      if (item.id === state.conversationId) li.className = "active";
      var name = document.createElement("div");
      name.className = "name";
      name.textContent = item.title;
      var when = document.createElement("div");
      when.className = "when";
      when.textContent = item.relative;
      var text = document.createElement("div");
      text.className = "row-text";
      text.appendChild(name);
      text.appendChild(when);
      li.appendChild(text);

      var remove = document.createElement("button");
      remove.className = "row-delete";
      remove.title = "Delete this conversation";
      remove.setAttribute("aria-label", "Delete " + item.title);
      remove.innerHTML = TRASH_SVG;
      remove.addEventListener("click", function (event) {
        event.stopPropagation();   // deleting is not opening
        if (!window.confirm("Delete “" + item.title + "”?")) return;
        backend.deleteConversation(item.id, function () {
          if (state.conversationId === item.id) {
            state.conversationId = null;
            $("title").textContent = "New conversation";
            renderMessages([]);
          }
          refreshConversations();
        });
      });
      li.appendChild(remove);

      li.addEventListener("click", function () { openConversation(item.id); });
      li.addEventListener("contextmenu", function (event) {
        event.preventDefault();
        var title = window.prompt("Rename conversation", item.title);
        if (title === null || title === "") return;
        backend.renameConversation(item.id, title, refreshConversations);
      });
      list.appendChild(li);
    });
    $("count").textContent = items.length + " conversation" +
      (items.length === 1 ? "" : "s");
    $("delete-all").classList.toggle("hidden", items.length === 0);
  }

  // -- data --------------------------------------------------------------

  function refreshConversations() {
    backend.listConversations($("search").value || "", function (json) {
      renderConversations(JSON.parse(json));
    });
  }

  function openConversation(id) {
    backend.loadConversation(id, function (json) {
      var data = JSON.parse(json);
      state.conversationId = data.id;
      $("title").textContent = data.title || "Conversation";
      renderMessages(data.messages || []);
      refreshConversations();
    });
  }

  function openLatest() {
    backend.listConversations("", function (json) {
      var items = JSON.parse(json);
      if (items.length) openConversation(items[0].id);
      else renderMessages([]);
    });
  }

  // -- live updates ------------------------------------------------------

  function setStatus(text, className) {
    var status = $("status");
    status.textContent = text;
    status.className = "status faint " + (className || "");
    var busy = className === "thinking" || className === "speaking";
    $("stop").classList.toggle("hidden", !busy);
    state.busy = busy;
  }

  function streamingNode() {
    var existing = document.getElementById("streaming");
    if (existing) return existing;
    var node = document.createElement("div");
    node.className = "msg assistant";
    node.id = "streaming";
    node.innerHTML = '<div class="who">Assistant</div><div class="body"></div>';
    $("messages").appendChild(node);
    return node;
  }

  function wireSignals() {
    backend.stateChanged.connect(function (value) {
      var labels = {
        idle: "Listening", awaiting_command: "Listening for your question…",
        thinking: "Thinking…", speaking: "Speaking…"
      };
      setStatus(labels[value] || value, value);
      if (value === "idle") {
        state.streaming = "";
        var node = document.getElementById("streaming");
        if (node) node.remove();
      }
    });
    backend.tokenStreamed.connect(function (token) {
      state.streaming += token;
      var node = streamingNode();
      node.querySelector(".body").innerHTML = markdown.render(state.streaming);
      var list = $("messages");
      list.scrollTop = list.scrollHeight;
    });
    backend.answerFinished.connect(function (json) {
      var data = JSON.parse(json);
      state.streaming = "";
      var node = document.getElementById("streaming");
      if (node) node.remove();
      if (data.conversation_id) openConversation(data.conversation_id);
    });
    backend.conversationsChanged.connect(refreshConversations);
    backend.errorOccurred.connect(function (json) {
      setStatus(JSON.parse(json).message, "error");
    });
  }

  // -- composer ----------------------------------------------------------

  function send() {
    var input = $("input");
    var text = input.value.trim();
    if (!text) return;
    input.value = "";
    input.style.height = "auto";
    backend.send(text, function () { refreshConversations(); });
  }

  function wireComposer() {
    $("send").addEventListener("click", send);
    $("input").addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        send();
      }
    });
    $("input").addEventListener("input", function () {
      this.style.height = "auto";
      this.style.height = Math.min(160, this.scrollHeight) + "px";
    });
    $("stop").addEventListener("click", function () { backend.stop(function () {}); });
    $("new-chat").addEventListener("click", function () {
      backend.newConversation(function (id) { openConversation(id); });
    });
    $("search").addEventListener("input", refreshConversations);
    $("delete-all").addEventListener("click", function () {
      if (!window.confirm("Delete every stored conversation? This cannot be " +
                          "undone.")) return;
      backend.deleteAllConversations(function () {
        state.conversationId = null;
        $("title").textContent = "New conversation";
        renderMessages([]);
        refreshConversations();
      });
    });
    $("export").addEventListener("click", function () {
      if (!state.conversationId) return;
      backend.exportConversation(state.conversationId, function (text) {
        navigator.clipboard.writeText(text);
        setStatus("Markdown copied to the clipboard", "");
      });
    });
    var ptt = $("ptt");
    ptt.addEventListener("mousedown", function () {
      ptt.classList.add("active");
      backend.pushToTalk(true, function () {});
    });
    ["mouseup", "mouseleave"].forEach(function (name) {
      ptt.addEventListener(name, function () {
        if (!ptt.classList.contains("active")) return;
        ptt.classList.remove("active");
        backend.pushToTalk(false, function () {});
      });
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") backend.stop(function () {});
    });
  }

  // -- boot ---------------------------------------------------------------

  connect(function () {
    backend.snapshot(function (json) {
      var snapshot = JSON.parse(json);
      $("provider").textContent = snapshot.provider.name + " · " +
        snapshot.provider.model;
      state.triggerWord = snapshot.trigger.words[0] || "";
      $("trigger-hint").textContent = "Say “" +
        snapshot.trigger.words.join("” or “") +
        "” — even on its own, to ask about what was just said";
      setStatus(snapshot.state === "idle" ? "Listening" : snapshot.state,
                snapshot.state);
    });
    wireSignals();
    wireComposer();
    openLatest();
    refreshConversations();
  });
})();
