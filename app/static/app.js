const source = document.querySelector('#source');
const task = document.querySelector('#task');
const status = document.querySelector('#status');
const review = document.querySelector('#review');
const details = document.querySelector('#details');
const confirmButton = document.querySelector('#confirm');
let planId = null;

const show = (message) => { status.textContent = message; };

function applySimpleCopy() {
  document.querySelector('.side-title').textContent = 'Локальный AI видеоредактор';
  document.querySelector('.side-note')?.remove();
  document.querySelector('.topbar h1').textContent = 'Напиши что надо, сделаю сам';
  document.querySelector('.welcome p').textContent = 'Опишите задачу обычным языком — я отправлю в работу.';
  document.querySelector('#plan').textContent = 'В работу';
  document.querySelector('#instructions').innerHTML = `
    <div class="welcome"><h2>Как работать с видео</h2><p>Сначала положите видео в папку, затем напишите, что нужно сделать.</p></div>
    <div class="instruction-card"><h3>1. Скопируйте видео</h3><p>Скопируйте нужный видеофайл в папку <code>AI_Cut</code>.</p></div>
    <div class="instruction-card"><h3>2. Выберите видео и напишите задачу</h3><p>Выберите файл в списке и опишите задачу обычными словами.</p><p>Например: «сделай MP4 без логотипа» или «оставь фрагмент с 1.10 до 2.30».</p></div>
    <div class="instruction-card"><h3>3. Проверьте план</h3><p>Программа покажет, что именно будет сделано. Если всё правильно, нажмите «Подтвердить».</p></div>
    <div class="instruction-card"><h3>4. Дождитесь результата</h3><p>Текущая обработка появится внизу экрана в разделе «В работе». Завершённые задания будут в истории слева.</p></div>
    <div class="instruction-card"><h3>Примеры задач</h3><p><code>Сделай MP4</code></p><p><code>Удали логотип</code></p><p><code>Добавь логотип</code></p><p><code>Оставь фрагмент с 0.15 до 0.50</code></p><p><code>Склей 0.15–0.50 и 1.25–2.13</code></p><p><code>Сделай MP4 1080p без логотипа</code></p></div>
    <div class="instruction-card"><h3>Как писать время</h3><p><code>1.10</code> — 1 минута 10 секунд.</p><p><code>01.23.23</code> — 1 час 23 минуты 23 секунды.</p><p>Для нескольких фрагментов перечислите их через «и».</p></div>`;
}

applySimpleCopy();

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
