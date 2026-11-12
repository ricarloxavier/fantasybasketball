(() => {
    const AWARD_INFO = {
        putilla_suprema: { emoji: "👑", title: "Putilla Suprema", description: "Most weeks with putillas rostered.", rank: "gold" },
        putilla_de_plata: { emoji: "🥈", title: "Putilla de Plata", description: "Second most weeks with putillas rostered.", rank: "silver" },
        putilla_de_bronce: { emoji: "🥉", title: "Putilla de Bronce", description: "Third most weeks with putillas rostered.", rank: "bronze" },
        putilla_mas_activa: { emoji: "⚡", title: "Putilla Más Activa", description: "Most distinct putillas rostered. Roster turnover king.", rank: "gold" },
        putilla_fiel: { emoji: "💎", title: "Putilla Fiel", description: "Fewest distinct putillas rostered. Loyal to the bitter end.", rank: "gold" },
        el_putilla_housewife: { emoji: "🏠", title: "El Putilla Housewife", description: "Most players added and never dropped.", rank: "gold" },
        la_putilla_escondida: { emoji: "💰", title: "La Putilla Escondida", description: "Best value per week. Found gold in the trash.", rank: "gold" },
        putilla_MVP: { emoji: "🌟", title: "Putilla MVP", description: "Highest total value from a putilla stint.", rank: "gold" },
        putilla_basura: { emoji: "🗑️", title: "Putilla Basura", description: "Worst value per week. We all make mistakes.", rank: "gold" },
    };
    const TEAM_ORDER = ["putilla_suprema", "putilla_de_plata", "putilla_de_bronce", "putilla_mas_activa", "putilla_fiel", "el_putilla_housewife"];
    const PLAYER_ORDER = ["la_putilla_escondida", "putilla_MVP", "putilla_basura"];
    const statusEl = document.getElementById("status");
    const panel = document.getElementById("awardsPanel");

    const el = (tag, cls, text) => {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text !== undefined) node.textContent = text;
        return node;
    };

    function formatStats(entry) {
        if (entry.games_started !== undefined && entry.player_name === "Team Total") return `${entry.games_started} weeks rostered`;
        if (entry.unique_players !== undefined) return `${entry.unique_players} players`;
        if (entry.total_value !== undefined) return `${Number(entry.total_value).toFixed(1)} total value · ${entry.games_started} weeks`;
        if (entry.value_per_game !== undefined) return `${Number(entry.value_per_game).toFixed(2)} value/week · ${entry.games_started} weeks`;
        return "";
    }

    function card(info, data) {
        const node = el("div", `award-card ${info.rank}`);
        node.append(el("div", "award-emoji", info.emoji), el("div", "award-title", info.title), el("div", "award-description", info.description));
        if (data.winner) {
            const w = data.winner;
            const isTeam = w.player_name === "Team Total";
            const box = el("div", "winner");
            box.append(el("div", "winner-name", isTeam ? w.team_name : w.player_name));
            box.append(el("div", "winner-team", isTeam ? "Team" : w.team_name));
            box.append(el("div", "winner-stats", formatStats(w)));
            if (isTeam && w.player_list && w.player_list.length) box.append(el("div", "player-list", `Players: ${w.player_list.join(", ")}`));
            node.append(box);
        }
        if (data.runners_up && data.runners_up.length) {
            const ru = el("div", "runner-up");
            ru.append(el("div", "runner-up-title", "Runners-up"));
            for (const r of data.runners_up.slice(0, 3)) {
                const isTeam = r.player_name === "Team Total";
                const line = isTeam ? `${r.team_name} · ${formatStats(r)}` : `${r.player_name} (${r.team_name}) · ${formatStats(r)}`;
                const entry = el("div", "runner-up-entry", line);
                if (isTeam && r.player_list && r.player_list.length) entry.append(el("div", "player-list", r.player_list.join(", ")));
                ru.append(entry);
            }
            node.append(ru);
        }
        return node;
    }

    function render(data) {
        document.getElementById("generated-at").textContent = `Last updated ${data.generated_at}${data.league_name ? " · " + data.league_name : ""} · week ${data.current_week} · ${data.total_stints} putilla stints`;
        const fill = (gridId, order, awards) => {
            const grid = document.getElementById(gridId);
            grid.replaceChildren();
            for (const key of order) {
                if (AWARD_INFO[key] && awards && awards[key]) grid.append(card(AWARD_INFO[key], awards[key]));
            }
        };
        fill("team-awards-grid", TEAM_ORDER, data.team_awards);
        fill("player-awards-grid", PLAYER_ORDER, data.player_awards);
        panel.style.display = "grid";
    }

    async function load(refresh) {
        statusEl.className = "status";
        statusEl.textContent = refresh ? "Recalculating from Yahoo (this takes a minute)…" : "Loading awards…";
        try {
            const resp = await fetch(refresh ? "/api/awards/refresh" : "/api/awards");
            const payload = await resp.json();
            if (!resp.ok || !payload.success) throw new Error(payload.error || `HTTP ${resp.status}`);
            render(payload.data);
            statusEl.textContent = "Up to date.";
        } catch (err) {
            statusEl.className = "status error";
            statusEl.textContent = err.message || "Could not load awards.";
        }
    }

    document.getElementById("refreshBtn").addEventListener("click", () => load(true));
    load(false);
})();
