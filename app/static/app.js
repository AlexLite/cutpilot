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
  const list = document.querySelector('#queue-list');
  queue.hidden = !data.jobs.length;
  list.replaceChildren(...data.jobs.slice(0, 20).map((job) => {
    const item = document.createElement('div');
    item.className = 'queue-item';
    const name = document.createElement('span');
    name.textContent = job.source;
    const state = document.createElement('small');
    state.textContent = job.status === 'completed' ? `готово: ${job.message}` : job.status === 'failed' ? `ошибка: ${job.message}` : 'обрабатывается';
    item.append(name, state);
    return item;
  }));
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
