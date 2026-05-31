/* ============================================================
   DirectoryBrowser — shared raster directory/file tree.
   Used by the Land Suitability and Land Similarity pages.

   This file is page-agnostic and contains NO Django template
   syntax. All server-derived values (API URLs) are passed in by
   the caller, which reads them from the #js-config json_script
   block rendered by the template.

   Usage:
     DirectoryBrowser.render({
       container: <DOM element>,
       rootPath: "",
       urls: { directoryContents, folderConfigurations },
       onFolderOpen:  (folderName, folderConfig) => {},   // optional
       onFileSelect:  (filePath, item) => {},
       onFileDeselect:(filePath) => {},
     });
   ============================================================ */
(function (window) {
  "use strict";

  /** Turn an arbitrary file path into a safe id/selector token. */
  function sanitizeFilePath(filePath) {
    return filePath.replace(/[^a-zA-Z0-9]/g, "_");
  }

  async function fetchJSON(url) {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Request failed (${response.status}): ${url}`);
    }
    return response.json();
  }

  /** Fetch the contents of one directory. Returns [] on failure. */
  async function fetchDirectoryContents(baseUrl, directoryPath) {
    try {
      return await fetchJSON(`${baseUrl}?path=${encodeURIComponent(directoryPath)}`);
    } catch (error) {
      console.error("Error fetching directory contents:", error);
      alert("Failed to load directory contents. Please try again later.");
      return [];
    }
  }

  /** Fetch the map center/zoom configuration for a folder. */
  async function fetchFolderConfigurations(baseUrl, folderName) {
    return fetchJSON(`${baseUrl}?folder=${encodeURIComponent(folderName)}`);
  }

  function renderFolder(li, item, directoryPath, opts) {
    const childPath = `${directoryPath}/${item.name}`;
    li.classList.add("folder");
    // Expose the bare name on the dataset so the search filter can match
    // against it without parsing the rendered icons out of innerHTML.
    li.dataset.name = item.name;
    li.style.cursor = "pointer";
    li.innerHTML =
      '<i class="fas fa-caret-right folder-icon mr-2"></i>' +
      '<i class="fas fa-folder mr-2"></i>' +
      item.name;

    const folderContent = document.createElement("div");
    folderContent.classList.add("folder-content");
    li.appendChild(folderContent);

    li.addEventListener("click", async (event) => {
      event.stopPropagation();

      if (!li.dataset.loaded) {
        const sub = await fetchDirectoryContents(opts.urls.directoryContents, childPath);
        buildTree(sub, folderContent, childPath, opts);
        li.dataset.loaded = "true";
      }

      li.classList.toggle("expanded");
      folderContent.style.display =
        folderContent.style.display === "block" ? "none" : "block";

      const icon = li.querySelector(".folder-icon");
      icon.classList.toggle("fa-caret-right");
      icon.classList.toggle("fa-caret-down");

      if (typeof opts.onFolderOpen === "function" && opts.urls.folderConfigurations) {
        try {
          const config = await fetchFolderConfigurations(
            opts.urls.folderConfigurations,
            item.name
          );
          opts.onFolderOpen(item.name, config);
        } catch (e) {
          console.error("Folder configuration lookup failed:", e);
        }
      }
    });
  }

  function renderFile(li, item, directoryPath, opts) {
    const filePath = `${directoryPath}/${item.name}`;
    li.classList.add("file");
    li.dataset.name = item.name;

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = filePath;
    checkbox.classList.add("mr-2");
    checkbox.addEventListener("click", (event) => event.stopPropagation());
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        if (typeof opts.onFileSelect === "function") opts.onFileSelect(filePath, item);
      } else if (typeof opts.onFileDeselect === "function") {
        opts.onFileDeselect(filePath);
      }
    });

    const label = document.createElement("label");
    label.classList.add("file-name");
    label.textContent = item.name;
    label.setAttribute("title", item.name);

    li.appendChild(checkbox);
    li.appendChild(label);

    if (typeof opts.onFileInfo === "function") {
      const infoBtn = document.createElement("button");
      infoBtn.type = "button";
      infoBtn.className = "file-info-btn";
      infoBtn.title = "Layer metadata";
      infoBtn.innerHTML = '<i class="fas fa-info-circle"></i>';
      infoBtn.addEventListener("click", (event) => {
        event.stopPropagation();
        opts.onFileInfo(filePath, item);
      });
      li.appendChild(infoBtn);
    }
  }

  function buildTree(contents, parentElement, directoryPath, opts) {
    const ul = document.createElement("ul");
    ul.classList.add("list-group");

    contents.forEach((item) => {
      const li = document.createElement("li");
      li.classList.add("list-group-item");

      if (item.type === "directory") {
        renderFolder(li, item, directoryPath, opts);
      } else if (item.type === "file") {
        renderFile(li, item, directoryPath, opts);
      }
      ul.appendChild(li);
    });

    parentElement.appendChild(ul);
  }

  /** Render the directory tree into a container element. */
  async function render(opts) {
    if (!opts || !opts.container) {
      console.error("DirectoryBrowser.render: 'container' is required.");
      return;
    }
    if (!opts.urls || !opts.urls.directoryContents) {
      console.error("DirectoryBrowser.render: 'urls.directoryContents' is required.");
      return;
    }

    const rootPath = opts.rootPath || "";
    const contents = await fetchDirectoryContents(
      opts.urls.directoryContents,
      rootPath || "/"
    );
    buildTree(contents, opts.container, rootPath, opts);
  }

  /** Filter the rendered tree by ``query`` (case-insensitive, matches the
   *  item name). Hides non-matching items and reveals ancestor folders
   *  of matches. Auto-expands folders that contain matches.
   *
   *  Limitation: the tree is lazy-loaded — only what the user has already
   *  expanded participates in the search. This is intentional v1 behaviour;
   *  pre-fetching every country folder on page load would defeat the lazy
   *  load. The search input UI should hint at this.
   */
  function filter(container, query) {
    query = (query || "").trim().toLowerCase();
    const lis = container.querySelectorAll("li");
    if (!query) {
      lis.forEach((li) => { li.style.display = ""; });
      return;
    }
    // Mark every li as hidden-by-default, then unhide matches and their
    // ancestor chain.
    lis.forEach((li) => { li.dataset._hide = "1"; });
    lis.forEach((li) => {
      const name = (li.dataset.name || "").toLowerCase();
      if (name && name.includes(query)) {
        let cur = li;
        while (cur && cur !== container) {
          if (cur.tagName === "LI") {
            delete cur.dataset._hide;
            // Auto-expand ancestor folders so the match is visible.
            if (cur.classList.contains("folder")
                && !cur.classList.contains("expanded")) {
              const fc = cur.querySelector(".folder-content");
              const icon = cur.querySelector(".folder-icon");
              if (fc) fc.style.display = "block";
              if (icon) {
                icon.classList.remove("fa-caret-right");
                icon.classList.add("fa-caret-down");
              }
              cur.classList.add("expanded");
            }
          }
          cur = cur.parentElement;
        }
      }
    });
    lis.forEach((li) => {
      li.style.display = li.dataset._hide ? "none" : "";
      delete li.dataset._hide;
    });
  }

  window.DirectoryBrowser = {
    render: render,
    filter: filter,
    sanitizeFilePath: sanitizeFilePath,
    fetchDirectoryContents: fetchDirectoryContents,
    fetchFolderConfigurations: fetchFolderConfigurations,
  };
})(window);
