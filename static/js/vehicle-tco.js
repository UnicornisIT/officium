(() => {
  const data = document.getElementById('tco-data');
  if (!data || !window.Chart) return;
  const report = JSON.parse(data.textContent), charts = {};
  const labels = report.monthly.map(r => new Date(r.month).toLocaleDateString('ru-RU', {month:'short',year:'numeric'}));
  const colors = ['#477fe0','#e58b36','#3ca587','#b573cd','#d25d72','#879d35','#47a3ba','#946d4e','#717b91','#cfab3e'];
  function render(key, type, chartLabels, datasets, extra={}) {
    const canvas = document.getElementById('tco-'+key);
    if (!canvas) return;
    charts[key] = new Chart(canvas, {type, data:{labels:chartLabels,datasets}, options:{responsive:true,maintainAspectRatio:false,animation:false, ...extra}});
  }
  function theme() {
    const color = getComputedStyle(document.body).color;
    Chart.defaults.color = color;
    Object.values(charts).forEach(chart => {chart.options.plugins = {legend:{labels:{color}}}; chart.update();});
  }
  Chart.defaults.color = getComputedStyle(document.body).color;
  render('monthly','bar',labels,[{label:'Личные, ₽',data:report.monthly.map(r=>Number(r.total_self)),backgroundColor:colors[0]}, {label:'Организация, ₽',data:report.monthly.map(r=>Number(r.total_organization)),backgroundColor:colors[1]}],{scales:{x:{stacked:true},y:{stacked:true,beginAtZero:true}}});
  render('kinds','doughnut',report.kinds.map(([key,label])=>label),[{data:report.kinds.map(([k])=>report.vehicles.reduce((s,v)=>s+Number(v.by_kind[k]),0)),backgroundColor:colors}]);
  render('distance','line',labels,[{label:'₽/км',data:report.monthly.map(r=>r.cost_per_km===null?null:Number(r.cost_per_km)),borderColor:colors[2],spanGaps:false}],{scales:{y:{beginAtZero:true}}});
  document.querySelectorAll('[data-chart]').forEach(row => row.addEventListener('focus', () => {
    const chart = charts[row.dataset.chart], index = Number(row.dataset.index);
    const active = chart.data.datasets.map((_,datasetIndex)=>({datasetIndex,index}));
    chart.setActiveElements(active);
    chart.tooltip.setActiveElements(active,{x:chart.width/2,y:chart.height/2});
    chart.update();
  }));
  document.querySelectorAll('[data-chart]').forEach(row => row.addEventListener('blur', () => {
    const chart = charts[row.dataset.chart];
    chart.setActiveElements([]);
    chart.tooltip.setActiveElements([],{x:0,y:0});
    chart.update();
  }));
  new MutationObserver(theme).observe(document.documentElement,{attributes:true,attributeFilter:['data-bs-theme','data-theme']});
})();
