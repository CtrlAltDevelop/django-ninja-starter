/* The admin's live corner: a bell, a count, and toasts, on every page.
 *
 * Rendered by infrastructure.common.adminlive.live_script, which is why the two
 * paths below are template variables: they are settings, and a static file
 * would have to be told them at runtime anyway.
 *
 * Both sockets authenticate with the admin's own session cookie, so there is no
 * token here and nothing to refresh: an agent who signs out of the admin loses
 * the feeds with the session that carried them.
 *
 * No framework and no styling beyond inline rules, on purpose. This runs inside
 * whatever theme the project uses -- Unfold here -- and a widget that inherited
 * the theme's classes would break the first time the theme renamed one.
 */
(function () {
    "use strict";

    var NOTIFICATIONS_WS = "{{ notifications_ws_path|escapejs }}";
    var SUPPORT_WS = "{{ support_ws_path|escapejs }}";
    var CHAT_URL = "{{ chat_url|escapejs }}";
    var NOTIFICATIONS_URL = "{{ notifications_url|escapejs }}";

    if (!NOTIFICATIONS_WS && !SUPPORT_WS) { return; }

    var unread = { notifications: 0, support: 0 };
    /* Message ids either feed has already announced. A new support message is
     * published twice on purpose -- once down the support socket for whoever is
     * connected, and once as a notification for whoever is not -- and an agent
     * who is connected is therefore both. Without this the bell counts one
     * message as two and says the same sentence twice. */
    var announced = new Set();
    /* Which account these sockets belong to, learned from the support socket's
     * own `ready` frame. Without it the bell announces the agent's own replies
     * back to them -- the thread channel carries every message to everybody in
     * the thread, the author included. */
    var me = null;
    /* Until when a notification frame is assumed to be catch-up rather than
     * news. The notification socket answers `ready` and then replays everything
     * still unread, which is right for a client rendering a tray and wrong for a
     * toast: an agent who has four unread announcements should see a badge
     * saying four, not four pop-ups on every page they open. The count is still
     * taken from the server, so nothing is lost by staying quiet. */
    var catchUpUntil = 0;
    var bell = null;
    var count = null;
    var tray = null;

    /* Whether this page is the support desk, which announces its own traffic. */
    function onTheDesk() {
        return Boolean(CHAT_URL) && window.location.pathname === CHAT_URL;
    }

    function socketUrl(path) {
        var scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
        return scheme + "//" + window.location.host + path;
    }

    /* Where the bell goes, in order of preference: the theme's header bar, the
     * plain admin's user tools, and the corner of the window if this admin has
     * neither. The first two put it where somebody already looks for their
     * account and the environment badge; the third is what keeps the feed
     * working in an admin nobody has themed. */
    function mount() {
        var header = document.getElementById("header-inner");
        if (header) { return { node: header.firstElementChild || header, inline: true }; }
        var tools = document.getElementById("user-tools");
        if (tools) { return { node: tools, inline: true }; }
        return { node: document.body, inline: false };
    }

    function build() {
        var where = mount();

        bell = document.createElement("div");
        bell.style.cssText = where.inline
            // `margin-left:auto` rather than a float: the header's own row is a
            // flexbox, so this is what pushes the bell to the end of it whatever
            // the theme put there first.
            ? "align-items:center;display:flex;font-size:.875rem;margin-left:auto;position:relative"
            : "align-items:flex-end;bottom:1.25rem;display:flex;flex-direction:column;"
              + "font-size:.875rem;gap:.5rem;position:fixed;right:1.25rem;z-index:60";

        tray = document.createElement("div");
        tray.style.cssText = where.inline
            // Hung under the bell rather than in the page flow, so a toast never
            // stretches the header it is announced from.
            ? "display:flex;flex-direction:column;gap:.5rem;position:absolute;right:0;top:2.25rem;"
              + "width:20rem;max-width:80vw;z-index:60"
            : "display:flex;flex-direction:column;gap:.5rem;width:20rem;max-width:80vw";

        var button = document.createElement("button");
        button.type = "button";
        button.setAttribute("aria-label", "Live feed");
        button.style.cssText = where.inline
            // Transparent in the header: the theme's bar already has a
            // background, and a dark pill sitting on it would read as a button
            // somebody added by accident.
            ? "align-items:center;background:none;border:0;color:inherit;cursor:pointer;"
              + "display:flex;gap:.3rem;line-height:1;padding:.25rem"
            : "align-items:center;background:#1f2937;border:0;border-radius:9999px;color:#fff;"
              + "cursor:pointer;display:flex;gap:.4rem;padding:.55rem .9rem;"
              + "box-shadow:0 6px 20px rgba(0,0,0,.25)";
        button.textContent = "🔔";

        count = document.createElement("span");
        count.style.cssText = "background:#ef4444;border-radius:9999px;color:#fff;display:none;"
            + "font-size:.6875rem;line-height:1.2;padding:0 .35rem";
        button.append(count);

        button.addEventListener("click", function () {
            /* The bell is a shortcut to wherever the unanswered thing is: the
             * desk when a conversation is waiting, the notification list
             * otherwise. Clearing the toasts and going nowhere would be a
             * button that does nothing twice in a row. */
            if (unread.support && CHAT_URL) { window.location.href = CHAT_URL; return; }
            if (unread.notifications && NOTIFICATIONS_URL) {
                window.location.href = NOTIFICATIONS_URL;
                return;
            }
            tray.replaceChildren();
        });

        bell.append(button, tray);
        where.node.append(bell);
    }

    function draw() {
        /* The sum of two server-side counts, not a tally this page keeps. They
         * are counted separately because they are cleared separately: reading a
         * thread clears its messages, and a notification stays unread until it
         * is read. A support message that also raised a notification is
         * therefore two unanswered things, and is announced once. */
        var total = unread.notifications + unread.support;
        count.textContent = total > 99 ? "99+" : String(total);
        count.style.display = total ? "inline-block" : "none";
    }

    function toast(title, body, href) {
        var card = document.createElement("div");
        card.style.cssText = [
            "background:#111827", "border-radius:.5rem", "color:#f9fafb", "cursor:pointer",
            "padding:.7rem .8rem", "box-shadow:0 6px 20px rgba(0,0,0,.25)"
        ].join(";");
        var head = document.createElement("div");
        head.style.cssText = "font-weight:600";
        head.textContent = title;
        var text = document.createElement("div");
        text.style.cssText = "opacity:.8;margin-top:.15rem";
        /* textContent throughout: every string here was written by whoever
         * opened the ticket, and a toast built with innerHTML is a stored XSS
         * with a nicer name. */
        text.textContent = body || "";
        card.append(head, text);
        card.addEventListener("click", function () {
            card.remove();
            if (href) { window.location.href = href; }
        });
        tray.append(card);
        window.setTimeout(function () { card.remove(); }, 10000);
    }

    function listen(path, onFrame) {
        if (!path) { return; }
        var wait = 2000;
        function open() {
            var socket = new WebSocket(socketUrl(path));
            socket.addEventListener("open", function () { wait = 2000; });
            socket.addEventListener("message", function (event) {
                var frame;
                try { frame = JSON.parse(event.data); } catch (error) { return; }
                onFrame(frame, socket);
            });
            socket.addEventListener("close", function (event) {
                /* 1008 is the support socket refusing a handshake it cannot
                 * name -- a signed-out tab. Retrying that forever would be a
                 * connection attempt every two seconds for as long as the tab
                 * is open, and it would never succeed. */
                if (event.code === 1008 || event.code === 4404) { return; }
                window.setTimeout(open, wait);
                wait = Math.min(wait * 2, 60000);
            });
            /* A proxy that drops quiet connections should not be what ends the
             * feed, and both sockets answer ping with pong. */
            window.setInterval(function () {
                if (socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({ command: "ping" }));
                }
            }, 45000);
        }
        open();
    }

    function start() {
        build();

        listen(NOTIFICATIONS_WS, function (frame, socket) {
            /* The count is asked for rather than kept: a client that added one
             * per frame would double what it shows the moment it reconnects,
             * because the socket answers `ready` with the number and then
             * replays the unread ones as frames. Both sockets answer `unread`,
             * so the badge is the server's number and stays right through a
             * reconnect, a second tab, and a notification read on a phone. */
            if (frame.type === "ready" || frame.type === "authenticated") {
                unread.notifications = frame.unread || 0;
                catchUpUntil = Date.now() + 2000;
                draw();
                return;
            }
            if (frame.type === "unread") {
                unread.notifications = frame.count || 0;
                draw();
                return;
            }
            if (frame.type === "notification") {
                socket.send(JSON.stringify({ command: "unread" }));
                if (Date.now() < catchUpUntil) { return; }
                var about = (frame.notification.data || {}).message;
                /* On the desk screen, support traffic is the desk's to announce:
                 * it draws the message in the thread that is open and toasts the
                 * ones that are not. Saying it again up here would be the same
                 * message in two corners of one screen. */
                if (about && onTheDesk()) { return; }
                /* A new support message is published twice on purpose: down the
                 * support socket for whoever is connected, and as a
                 * notification for whoever is not. An agent with the admin open
                 * is both, and should hear it once. */
                if (announced.has(about)) { return; }
                if (about) { announced.add(about); }
                /* `link` is where the *client* reads this -- the support app
                 * writes `/support/tickets/<id>`, a route a customer's widget
                 * serves and this admin does not. An agent pressing it would
                 * land on a 404, so a notification about a conversation goes to
                 * the desk instead, and everything else keeps its own link. */
                var where = about && CHAT_URL ? CHAT_URL : frame.notification.link || "";
                toast(frame.notification.subject, frame.notification.body, where);
            }
        });

        listen(SUPPORT_WS, function (frame, socket) {
            if (frame.type === "ready") {
                me = frame.user ? frame.user.id : null;
                unread.support = frame.unread ? frame.unread.messages || 0 : 0;
                draw();
                return;
            }
            if (frame.type === "unread") {
                unread.support = frame.messages || 0;
                draw();
                return;
            }
            if (frame.type === "message") {
                if (me && frame.message.author && frame.message.author.id === me) { return; }
                socket.send(JSON.stringify({ command: "unread" }));
                /* The desk screen draws its own messages and counts its own
                 * badge; a second toast over the top of the conversation the
                 * agent is reading is noise. */
                if (onTheDesk()) { return; }
                if (announced.has(frame.message.id)) { return; }
                announced.add(frame.message.id);
                var author = frame.message.author ? frame.message.author.username : "Support";
                toast(author + " wrote", (frame.message.body || "").slice(0, 140), CHAT_URL);
            }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
