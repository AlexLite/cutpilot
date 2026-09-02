const source = document.querySelector('#source');
const task = document.querySelector('#task');
const status = document.querySelector('#status');
const review = document.querySelector('#review');
const details = document.querySelector('#details');
const confirmButton = document.querySelector('#confirm');
let planId = null;

const show = (message) => { status.textContent = message; };

document.querySelectorAll('.tab').forEach((button) => button.addEventListener('click', () => {
  document.querySelectorAll('.tab, .pane').forEach((element) => element.classList.remove('active'));
  button.classList.add('active');
  document.getElementById(button.dataset.tab).classList.add('active');
}));

async function loadFiles() {
  const response = await fetch('/api/files');
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Не удалось получить список');
  source.replaceChildren(...data.files.map(file => new Option(`${file.name} (${file.size} bytes)`, file.name)));
  if (!data.files.length) show('В AI_Cut пока нет видео.');
}

async function loadJobs() {
  const response = await fetch('/api/jobs');
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Не удалось получить очередь');
  const queue = document.querySelector('#queue');
  const activeList = document.querySelector('#active-list');
  const historySidebar = document.querySelector('#history-sidebar');
  const historyList = document.querySelector('#history-list');
  const active = data.jobs.filter((job) => ['queued', 'processing', 'cancelling'].includes(job.status));
  const history = data.jobs.filter((job) => !['queued', 'processing', 'cancelling'].includes(job.status));
  const renderJob = (job) => {
    const item = document.createElement('div');
    item.className = 'queue-item';
    const name = document.createElement('span');
    name.textContent = job.source;
    const state = document.createElement('small');
    state.textContent = job.status === 'completed' ? `готово: ${job.message}` : job.status === 'failed' ? `ошибка: ${job.message}` : job.status === 'cancelled' ? 'отменено' : job.status === 'queued' ? 'в очереди' : job.status === 'cancelling' ? 'останавливается' : (job.progress ? `обрабатывается: ${job.progress}%` : 'обрабатывается');
    item.append(name, state);
    if (job.status === 'processing') {
      const cancel = document.createElement('button');
      cancel.className = 'cancel';
      cancel.textContent = 'Остановить';
      cancel.onclick = async () => {
        cancel.disabled = true;
        await fetch('/api/jobs/cancel', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: job.id})});
        loadJobs().catch(() => {});
      };
      item.append(cancel);
    }
    return item;
  };
  activeList.replaceChildren(...active.slice(0, 20).map(renderJob));
  historyList.replaceChildren(...history.slice(0, 20).map(renderJob));
  historySidebar.hidden = !history.length;
  queue.hidden = !active.length;
}

document.querySelector('#plan').onclick = async () => {
  show('Запрашиваю план…');
  review.hidden = true;
  try {
    const response = await fetch('/api/plan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({source: source.value, task: task.value}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    planId = data.plan_id;
    details.textContent = `Исходник: ${data.source_filename}\nПередача в CutPilot: ${data.staged_filename}\nКоманды: ${data.commands.join(' ') || '(без команд; стандартная обработка)'}\nПояснение: ${data.summary || '(нет)'}`;
    review.hidden = false;
    confirmButton.style.display = 'block';
    show('Проверьте имя и команды.');
  } catch (error) {
    show(`Ошибка: ${error.message}`);
  }
};

confirmButton.onclick = async () => {
  if (!planId) return;
  const response = await fetch('/api/jobs', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({plan_id: planId, confirmed: true}),
  });
  const data = await response.json();
  show(response.ok ? `Поставлено в очередь: ${data.filename}` : `Ошибка: ${data.error}`);
  if (response.ok) {
    planId = null;
    confirmButton.style.display = 'none';
  }
};

loadFiles().catch(error => show(`Ошибка списка файлов: ${error.message}`));
loadJobs().catch(() => {});
setInterval(() => loadJobs().catch(() => {}), 5000);
