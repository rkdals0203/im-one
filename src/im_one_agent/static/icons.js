(function () {
  const ICONS = {
    "arrow-up": '<path d="M12 19V5"></path><path d="m5 12 7-7 7 7"></path>',
    "book-open": '<path d="M12 7v14"></path><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"></path>',
    check: '<path d="M20 6 9 17l-5-5"></path>',
    "chevron-down": '<path d="m6 9 6 6 6-6"></path>',
    copy: '<rect width="14" height="14" x="8" y="8" rx="2"></rect><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><path d="M7 10l5 5 5-5"></path><path d="M12 15V3"></path>',
    "file-text": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"></path><path d="M14 2v4a2 2 0 0 0 2 2h4"></path><path d="M10 9H8"></path><path d="M16 13H8"></path><path d="M16 17H8"></path>',
    "list-filter": '<path d="M3 6h18"></path><path d="M7 12h10"></path><path d="M10 18h4"></path>',
    moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"></path>',
    play: '<polygon points="6 3 20 12 6 21 6 3"></polygon>',
    "rotate-ccw": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"></path><path d="M3 3v5h5"></path>',
    search: '<circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.3-4.3"></path>',
    "send-horizontal": '<path d="m3 3 3 9-3 9 19-9Z"></path><path d="M6 12h16"></path>',
    sparkles: '<path d="m12 3-1.9 5.8L4 11l6.1 2.2L12 19l1.9-5.8L20 11l-6.1-2.2Z"></path><path d="M5 3v4"></path><path d="M3 5h4"></path><path d="M19 17v4"></path><path d="M17 19h4"></path>',
    sun: '<circle cx="12" cy="12" r="4"></circle><path d="M12 2v2"></path><path d="M12 20v2"></path><path d="m4.93 4.93 1.41 1.41"></path><path d="m17.66 17.66 1.41 1.41"></path><path d="M2 12h2"></path><path d="M20 12h2"></path><path d="m6.34 17.66-1.41 1.41"></path><path d="m19.07 4.93-1.41 1.41"></path>',
    "table-2": '<path d="M9 3H5a2 2 0 0 0-2 2v4"></path><path d="M9 3h10a2 2 0 0 1 2 2v4"></path><path d="M9 3v18"></path><path d="M3 9h18"></path><path d="M3 15h18"></path><path d="M3 9v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9"></path>',
    "thumbs-down": '<path d="M17 14V2"></path><path d="M9 18.12 10 14H4.5a2.5 2.5 0 0 1-2.42-3.11l1.2-4.8A4 4 0 0 1 7.16 3H17v11h-2.34a3 3 0 0 0-2.12.88L9 18.12a2 2 0 0 1 0-2.83"></path><path d="M21 2v12"></path>',
    "thumbs-up": '<path d="M7 10v12"></path><path d="M15 5.88 14 10h5.5a2.5 2.5 0 0 1 2.42 3.11l-1.2 4.8A4 4 0 0 1 16.84 21H7V10h2.34a3 3 0 0 0 2.12-.88L15 5.88a2 2 0 0 1 0 2.83"></path><path d="M3 10v12"></path>',
    workflow: '<rect width="8" height="8" x="3" y="3" rx="2"></rect><rect width="8" height="8" x="13" y="13" rx="2"></rect><path d="M11 7h4a2 2 0 0 1 2 2v4"></path><path d="M7 11v2a2 2 0 0 0 2 2h4"></path>',
  };

  function createIcon(name) {
    const body = ICONS[name] || '<circle cx="12" cy="12" r="8"></circle>';
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = body;
    return svg;
  }

  window.lucide = {
    createIcons() {
      document.querySelectorAll("i[data-lucide]").forEach((placeholder) => {
        const icon = createIcon(placeholder.dataset.lucide);
        placeholder.replaceWith(icon);
      });
    },
  };
})();
