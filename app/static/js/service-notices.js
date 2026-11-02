(function () {
  "use strict";

  const composer = document.getElementById("service-notice-form");
  const searchForm = document.getElementById("customer-search-form");
  const searchInput = document.getElementById("customer-search");
  const clearSearch = document.getElementById("customer-search-clear");
  const tableBody = document.getElementById("candidate-table-body");
  const pagination = document.getElementById("candidate-pagination");
  const results = document.getElementById("candidate-results");
  const selectionCount = document.getElementById("recipient-selection-count");
  const hiddenInputs = document.getElementById("selected-customer-inputs");

  if (
    !composer ||
    !searchForm ||
    !searchInput ||
    !clearSearch ||
    !tableBody ||
    !pagination ||
    !results ||
    !selectionCount ||
    !hiddenInputs ||
    !window.fetch
  ) {
    return;
  }

  const maxRecipients = Number.parseInt(composer.dataset.maxRecipients || "100", 10);
  const selected = new Set();

  function candidateCheckboxes() {
    return tableBody.querySelectorAll(".candidate-checkbox");
  }

  function syncSelectedInputs() {
    hiddenInputs.replaceChildren();
    selected.forEach(function (customerId) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "customer_ids";
      input.value = customerId;
      hiddenInputs.appendChild(input);
    });
  }

  function updateSelectionState() {
    selectionCount.textContent = selected.size + " of " + maxRecipients + " selected";
    candidateCheckboxes().forEach(function (checkbox) {
      const isSelected = selected.has(checkbox.value);
      checkbox.checked = isSelected;
      checkbox.disabled = selected.size >= maxRecipients && !isSelected;
    });
    syncSelectedInputs();
  }

  function handleSelection(event) {
    const checkbox = event.currentTarget;
    if (checkbox.checked) {
      if (selected.size >= maxRecipients && !selected.has(checkbox.value)) {
        checkbox.checked = false;
      } else {
        selected.add(checkbox.value);
      }
    } else {
      selected.delete(checkbox.value);
    }
    updateSelectionState();
  }

  function activateCheckboxes() {
    candidateCheckboxes().forEach(function (checkbox) {
      if (checkbox.checked) {
        selected.add(checkbox.value);
      }
      checkbox.removeAttribute("name");
      checkbox.addEventListener("change", handleSelection);
    });
    updateSelectionState();
  }

  function customerRow(customer) {
    const row = document.createElement("tr");

    const selectCell = document.createElement("td");
    const checkbox = document.createElement("input");
    checkbox.className = "candidate-checkbox";
    checkbox.type = "checkbox";
    checkbox.value = String(customer.customer_id);
    checkbox.setAttribute("aria-label", "Select " + (customer.customer_name || "customer"));
    selectCell.appendChild(checkbox);

    const customerCell = document.createElement("td");
    customerCell.appendChild(document.createTextNode(customer.customer_name || "Customer"));
    customerCell.appendChild(document.createElement("br"));
    const id = document.createElement("small");
    id.className = "text-muted";
    id.textContent = "PoolBrain ID " + customer.customer_id;
    customerCell.appendChild(id);

    const phoneCell = document.createElement("td");
    phoneCell.textContent = customer.phone_masked || "Unavailable";

    const consentCell = document.createElement("td");
    if (customer.preference_source) {
      consentCell.textContent = "Opt-in/preference recorded: " + customer.preference_source;
    } else {
      const warning = document.createElement("span");
      warning.className = "text-warning";
      warning.textContent = "Confirm documented consent";
      consentCell.appendChild(warning);
    }

    row.append(selectCell, customerCell, phoneCell, consentCell);
    return row;
  }

  function renderCustomers(items) {
    tableBody.replaceChildren();
    if (!items.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.className = "empty-state";
      cell.colSpan = 4;
      cell.textContent = "No eligible customers match this search.";
      row.appendChild(cell);
      tableBody.appendChild(row);
      updateSelectionState();
      return;
    }
    items.forEach(function (customer) {
      tableBody.appendChild(customerRow(customer));
    });
    activateCheckboxes();
  }

  function pageUrl(page, query) {
    const parameters = new URLSearchParams({ q: query, page: String(page) });
    return "/admin/service-notices?" + parameters.toString() + "#recipient-heading";
  }

  function paginationControl(label, page, disabled, options) {
    const element = document.createElement(disabled ? "span" : "a");
    element.textContent = label;
    if (disabled) {
      element.setAttribute("aria-disabled", "true");
    } else {
      element.href = pageUrl(page, searchInput.value.trim());
      element.dataset.candidatePage = String(page);
      if (options && options.rel) {
        element.rel = options.rel;
      }
      if (options && options.ariaLabel) {
        element.setAttribute("aria-label", options.ariaLabel);
      }
    }
    pagination.appendChild(element);
  }

  function renderPagination(data) {
    pagination.replaceChildren();
    pagination.hidden = data.pages <= 1;
    if (data.pages <= 1) {
      return;
    }
    paginationControl("First", 1, data.page <= 1, { ariaLabel: "First page" });
    paginationControl("Previous", data.page - 1, data.page <= 1, { rel: "prev" });
    const status = document.createElement("span");
    status.className = "pagination__status";
    status.textContent = "Page " + data.page + " of " + data.pages;
    pagination.appendChild(status);
    paginationControl("Next", data.page + 1, data.page >= data.pages, { rel: "next" });
    paginationControl("Last", data.pages, data.page >= data.pages, {
      ariaLabel: "Last page"
    });
  }

  function updateResults(data) {
    results.textContent = data.total
      ? "Showing " + data.first_item + "–" + data.last_item + " of " + data.total + " eligible customers"
      : "No matching customers";
  }

  async function loadCandidates(page, query) {
    const normalizedQuery = query.trim().slice(0, 100);
    tableBody.setAttribute("aria-busy", "true");
    results.textContent = "Loading customers…";
    try {
      const parameters = new URLSearchParams({ q: normalizedQuery, page: String(page) });
      const response = await fetch(
        "/admin/service-notices/candidates?" + parameters.toString(),
        { headers: { Accept: "application/json" }, credentials: "same-origin" }
      );
      if (!response.ok) {
        throw new Error("Customer list request failed");
      }
      const data = await response.json();
      searchInput.value = data.search;
      clearSearch.hidden = !data.search;
      clearSearch.href = pageUrl(1, "");
      renderCustomers(data.items);
      renderPagination(data);
      updateResults(data);
      window.history.replaceState(null, "", pageUrl(data.page, data.search));
    } catch (_error) {
      results.textContent = "The customer list could not be refreshed. Try again.";
    } finally {
      tableBody.removeAttribute("aria-busy");
    }
  }

  searchForm.addEventListener("submit", function (event) {
    event.preventDefault();
    loadCandidates(1, searchInput.value);
  });

  clearSearch.addEventListener("click", function (event) {
    event.preventDefault();
    searchInput.value = "";
    loadCandidates(1, "");
  });

  pagination.addEventListener("click", function (event) {
    const link = event.target.closest("[data-candidate-page]");
    if (!link) {
      return;
    }
    event.preventDefault();
    loadCandidates(Number.parseInt(link.dataset.candidatePage, 10), searchInput.value);
  });

  composer.addEventListener("submit", syncSelectedInputs);
  activateCheckboxes();
})();
