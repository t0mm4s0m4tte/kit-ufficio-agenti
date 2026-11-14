// Edificio 3D dell'ufficio: un condominio di notte coi neon, robottini alle scrivanie che vanno a trovare i colleghi.
// Sezioni: costanti · dati condivisi · scena e render · mattoni · robottini · vista esterna (piani a cassetto) · HUD ·
// vista piano · uffici vivi (regista e camminate) · colori dallo stato · navigazione · pannello e chat · ricerca ·
// elenco 2D · input · rete · avvio.
import * as THREE from 'three';

// ===== Costanti =====
const DIREZIONE = new THREE.Vector3(1, 1, 1).normalize();  // la camera guarda il centro da (1, 1, 1)
const DESTRA = new THREE.Vector3(1, 0, -1).normalize();    // asse orizzontale dello schermo
const SU = new THREE.Vector3(-1, 2, -1).normalize();       // asse verticale dello schermo
const ESTERNO = { larghezza: 24, profondita: 16, altezza: 4.5, solaio: 0.5, balcone: 1.2 };
const SCIVOLA = ESTERNO.profondita * 0.45;  // il piano sotto il mouse esce lungo +z, verso chi guarda
const RIDOTTO = matchMedia('(prefers-reduced-motion: reduce)').matches;
const DURATA = RIDOTTO ? 0 : 250;  // ms per uscire o rientrare
const PUNTATORE_GROSSO = matchMedia('(pointer: coarse)');
const telefono = () => innerWidth < 700 || PUNTATORE_GROSSO.matches;
// la pianta del server ha il passo di 2: qui la si allarga (fattori fissi) per robot piu' grandi e corridoi dove camminare
const ROBOT = 1.5;                               // scala dei robottini; capi 1.5x, Direttore 1.8x, comanager 1.2x sopra
const ALLARGA = { x: 1.25, z: 1.6, capi: 1.7 };  // colonne 2.5, file 3.2; l'ufficio dei capi un po' piu' in la'
const CORRIDOIO = 1.12;    // dalla sedia alla linea di corridoio dietro la fila (fra la sedia e la scrivania di dietro)
const CORSIA_SINISTRA = -2.4;  // corsia lungo z fra l'ufficio dei capi e la prima colonna
const PARETE_UFFICIO = -3.4;   // parete destra dell'ufficio dei capi
const VELOCITA = 4;      // unita' al secondo a piedi
const ALTEZZA_PARETE = 4.2;
const NOTTE = '#090b10';
const COLORI_STANZE = ['#cbd5e1', '#94a3b8', '#cbd5e1', '#94a3b8'];
const INTERVALLO_STATO = 3000;
// occhi e schermi: platino da fermi, ghiaccio quando lavorano, oro tenue in coda
const LUCI = {
  occhi: { fermo: '#cbd5e1', lavora: '#ecfdf5', coda: '#fef3c7' },
  schermo: { fermo: '#64748b', lavora: '#f8fafc', coda: '#e5d3a3' },
};
const NEON = { lavora: '#4ade80', coda: '#d4c08a', rosa: '#cbd5e1', ciano: '#e2e8f0', arancio: '#94a3b8' };

// ===== Dati condivisi =====
const agentiPerId = new Map();  // id -> agente della pianta, con il campo `piano`
let stato = { lavorando: [], in_coda: [], attesa_ram: false, ram_mb: 0, flussi: [], eventi: {}, statistiche: {} };  // ultimo /api/stato
let edificio = null;            // ultimo /api/edificio
let pianoAperto = null;         // vista piano: vedi costruisciPiano
let estratto = null;            // id del piano uscito dal blocco (hover, tocco o frecce); null = edificio chiuso
let ultimoPuntatore = 'mouse';  // 'mouse', 'touch', 'pen' o 'tastiera': cambia l'invito dell'HUD

const $ = (id) => document.getElementById(id);
const capoDi = (p) => p.agenti.find((a) => a.ruolo === 'direttore') ?? p.agenti.find((a) => a.ruolo === 'capo');
// chi e' in tutti e due lavora
function insiemi() {
  const lav = new Set(stato.lavorando);
  return { lav, coda: new Set(stato.in_coda.filter((id) => !lav.has(id))) };
}

// ===== Scena, camera e render =====
const contenitore = $('scena');
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
// sul telefono al massimo 1.5: meno pixel da riempire, meno calore
const densita = () => Math.min(devicePixelRatio, telefono() ? 1.5 : 2);
renderer.setPixelRatio(densita());
renderer.setClearColor(0x000000, 0);  // dietro la tela c'e' la notte, fatta in CSS
contenitore.prepend(renderer.domElement);
const tela = renderer.domElement;

const scena = new THREE.Scene();
const nebbia = new THREE.Fog('#0b0e14', 1060, 1420);  // solo fuori: la citta' lontana sfuma nella notte
const cielo = new THREE.HemisphereLight(), luna = new THREE.DirectionalLight(), riverbero = new THREE.DirectionalLight();
luna.position.set(-0.8, 1.6, 1.2);
riverbero.position.set(2.5, 0.6, 1.4);
scena.add(cielo, luna, riverbero);
// fuori: luna fredda argentea e riverbero platino; dentro: luce calda d'ufficio con un filo freddo neutro
function luciScena(dentro) {
  const [c, t, i, l, li, r, ri] = dentro
    ? ['#ffe8cc', '#232630', 1.5, '#ffdfb8', 1.5, '#cbd5e1', 0.45]
    : ['#cbd5e1', '#1a202c', 1.25, '#f8fafc', 1.45, '#94a3b8', 0.85];
  cielo.color.set(c); cielo.groundColor.set(t); cielo.intensity = i;
  luna.color.set(l); luna.intensity = li;
  riverbero.color.set(r); riverbero.intensity = ri;
  scena.fog = dentro ? null : nebbia;
}

const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 1, 4000);
const centro = new THREE.Vector3();
let estensione = { x: 10, y: 10 };  // mezza larghezza e mezza altezza da inquadrare, in unita' del mondo

function aggiornaCamera() {
  const w = contenitore.clientWidth || 1, h = contenitore.clientHeight || 1;
  const meta = Math.max(estensione.y, estensione.x * h / w);
  Object.assign(camera, { top: meta, bottom: -meta, left: -meta * w / h, right: meta * w / h });
  camera.position.copy(centro).addScaledVector(DIREZIONE, 1000);
  camera.lookAt(centro);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  richiediRender();
}

// inquadra la scatola; destra e sotto lasciano posto all'HUD (frazioni della larghezza e dell'altezza)
function inquadra(box, destra = 0, sotto = 0) {
  const c = box.getCenter(new THREE.Vector3());
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (let i = 0; i < 8; i++) {
    const p = new THREE.Vector3(i & 1 ? box.max.x : box.min.x, i & 2 ? box.max.y : box.min.y,
      i & 4 ? box.max.z : box.min.z).sub(c);
    const x = p.dot(DESTRA), y = p.dot(SU);
    x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  x1 += (x1 - x0) * destra;
  y0 -= (y1 - y0) * sotto;
  centro.copy(c).addScaledVector(DESTRA, (x0 + x1) / 2).addScaledVector(SU, (y0 + y1) / 2);
  estensione = { x: (x1 - x0) / 2 * 1.06, y: (y1 - y0) / 2 * 1.06 };
  camera.zoom = 1;
  aggiornaCamera();
}

// Nella vista piano il loop gira di continuo, ma solo con la scheda visibile e senza "riduci movimento";
// sul telefono a ~30 fps. Fuori gira a ~25 fps per i passanti (stesse condizioni), se no solo a richiesta.
let renderInAttesa = false, ultimoFrame = 0, ultimoFuori = 0;
// niente loop dietro l'elenco 2D (la scena e' nascosta)
const vivo = () => !!pianoAperto && !contenitore.hidden && !RIDOTTO && document.visibilityState === 'visible';
const fuoriVivo = () => !pianoAperto && !!passanti && esterno.visible && !contenitore.hidden && !RIDOTTO
  && document.visibilityState === 'visible';
function richiediRender() {
  if (renderInAttesa) return;
  renderInAttesa = true;
  requestAnimationFrame(disegna);
}
function disegna(ora) {
  renderInAttesa = false;
  let ancora = muoviCassetti(ora);
  if (vivo()) {
    if (telefono() && ora - ultimoFrame < 30) return richiediRender();
    animaPiano(ora, Math.min(0.1, Math.max(0, ora - ultimoFrame) / 1000));
    ultimoFrame = ora;
    ancora = true;
  } else if (fuoriVivo()) {
    // fra un passo e l'altro dei passanti non si disegna (a meno che un cassetto stia scivolando)
    if (!ancora && ora - ultimoFuori < 40) return richiediRender();
    muoviPassanti(Math.min(0.1, Math.max(0, ora - ultimoFuori) / 1000), ora / 1000);
    muoviFlussiEsterni(ora / 1000);
    ultimoFuori = ora;
    ancora = true;
  }
  if (pianoAperto) scalaOlogrammi();
  renderer.render(scena, camera);
  if (ancora) richiediRender();
}
document.addEventListener('visibilitychange', () => { if (vivo() || fuoriVivo()) richiediRender(); });

new ResizeObserver(() => {  // scatta anche quando il telefono gira
  renderer.setPixelRatio(densita());
  renderer.setSize(contenitore.clientWidth, contenitore.clientHeight, false);
  aggiornaCamera();
}).observe(contenitore);

// ===== Mattoni =====
const CUBO = new THREE.BoxGeometry(1, 1, 1);
const PIANO_1 = new THREE.PlaneGeometry(1, 1);
const SFERA = new THREE.SphereGeometry(0.5, 12, 8);
const CILINDRO = new THREE.CylinderGeometry(0.5, 0.5, 1, 14);
const OVALE = new THREE.CylinderGeometry(0.5, 0.5, 1, 22);
const OVALE_APERTO = new THREE.CylinderGeometry(0.5, 0.5, 1, 22, 1, true);
const materiali = new Map();
function materiale(colore, opzioni = {}) {
  const chiave = colore + JSON.stringify(opzioni);
  if (!materiali.has(chiave)) {
    materiali.set(chiave, new THREE.MeshStandardMaterial({ color: colore, roughness: 0.8, ...opzioni }));
  }
  return materiali.get(chiave);
}
const VETRO = { transparent: true, opacity: 0.2, roughness: 0.05, metalness: 0.4, depthWrite: false };
// neon, schermi e insegne: non sentono la luce, quindi sembrano accesi
const luci = new Map();
function luce(colore) {
  if (!luci.has(colore)) luci.set(colore, new THREE.MeshBasicMaterial({ color: colore }));
  return luci.get(colore);
}
// alone additivo (bagliori per terra, sotto l'auto, dietro i segnali)
function bagliore(mappa, colore, opacita = 1) {
  return new THREE.MeshBasicMaterial({ map: mappa, color: colore, transparent: true, opacity: opacita,
    blending: THREE.AdditiveBlending, depthWrite: false });
}
const mescola = (a, b, quanto) => `#${new THREE.Color(a).lerp(new THREE.Color(b), quanto).getHexString()}`;
const caso = (k) => { const s = Math.sin(k * 127.1 + 311.7) * 43758.5453; return s - Math.floor(s); };  // fisso

// scatola dagli estremi x0..x1, y0..y1, z0..z1
function scatola(x0, x1, y0, y1, z0, z1, mat) {
  const m = new THREE.Mesh(CUBO, mat);
  m.scale.set(x1 - x0, y1 - y0, z1 - z0);
  m.position.set((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
  return m;
}
function mesh(geo, mat, x, y, z, s = 1) {
  const m = new THREE.Mesh(geo, mat);
  m.position.set(x, y, z);
  if (typeof s === 'number') m.scale.setScalar(s); else m.scale.set(...s);
  return m;
}
// piano sdraiato a terra (y), lungo x e largo z, poi girato attorno a y
function aTerra(mat, x, y, z, lungo, largo, angolo = 0) {
  const m = new THREE.Mesh(PIANO_1, mat);
  m.rotation.set(-Math.PI / 2, angolo, 0, 'YXZ');  // prima si sdraia, poi si gira
  m.position.set(x, y, z);
  m.scale.set(lungo, largo, 1);
  return m;
}

// texture disegnata su un canvas 2D nel codice: niente immagini da scaricare
function disegnata(w, h, disegno) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  disegno(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  return t;
}
const ripetuta = (t, rx, ry = 1) => {
  const c = t.clone();
  c.wrapS = c.wrapT = THREE.RepeatWrapping;
  c.repeat.set(rx, ry);
  c.needsUpdate = true;
  return c;
};

// Scritta al neon su canvas, per i nomi di reparti, stanze e team (testi dall'API: fillText, mai HTML).
// Riempie il riquadro; se una riga sola verrebbe troppo piccola va a capo una volta, dove le righe sono piu' pari.
function scritta(testo, colore, larghezza, altezza, fondo, aSinistra = false) {
  const W = 1024, H = Math.round(W * altezza / larghezza);
  return disegnata(W, H, (g) => {
    if (fondo) {
      g.fillStyle = 'rgba(6, 9, 20, .82)';
      g.strokeStyle = colore;
      g.lineWidth = Math.max(4, H * 0.035);
      g.beginPath();
      g.roundRect(4, 4, W - 8, H - 8, H * 0.14);
      g.fill();
      g.stroke();
    }
    const font = (px) => `800 ${px}px system-ui, "Segoe UI", sans-serif`;
    const largo = (riga, px) => { g.font = font(px); return g.measureText(riga).width; };
    const utile = W * 0.9, adatta = (righe, max) => Math.min(max, ...righe.map((r) => max * utile / largo(r, max)));
    let righe = [testo], px = adatta(righe, H * 0.7);
    const parole = testo.split(' ');
    for (let k = 1; px < H * 0.42 && k < parole.length; k++) {
      const due = [parole.slice(0, k).join(' '), parole.slice(k).join(' ')], p2 = adatta(due, H * 0.42);
      if (p2 > px) [righe, px] = [due, p2];
    }
    g.font = font(px);
    g.textAlign = aSinistra ? 'left' : 'center';
    g.textBaseline = 'middle';
    const x = aSinistra ? W * 0.05 : W / 2;
    g.fillStyle = mescola(colore, '#ffffff', 0.45);
    for (const [blur, k] of [[px * 0.4, 0], [px * 0.08, 1]]) {  // alone, poi il tratto nitido
      g.shadowColor = colore;
      g.shadowBlur = blur;
      righe.forEach((r, i) => g.fillText(r, x, H / 2 + (i - (righe.length - 1) / 2) * px * 1.08));
      if (k) break;
    }
  });
}
// insegna verticale a filo di una parete che guarda +z
function insegna(testo, colore, x, y, z, larghezza, altezza) {
  const m = new THREE.Mesh(PIANO_1, new THREE.MeshBasicMaterial({ map: scritta(testo, colore, larghezza, altezza, true), transparent: true }));
  m.position.set(x, y, z);
  m.scale.set(larghezza, altezza, 1);
  return m;
}

// vetrate della facciata: vetro grigio-ardesia luminoso uniforme (plain), senza colori alternati
const FINESTRE = disegnata(512, 128, (g, w, h) => {
  g.fillStyle = '#1e2530';
  g.fillRect(0, 0, w, h);
  for (let i = 0; i < 4; i++) {
    const x = i * 128 + 4;
    const s = g.createLinearGradient(0, 8, 0, h - 8);
    s.addColorStop(0, '#465468');
    s.addColorStop(0.55, '#343f4f');
    s.addColorStop(1, '#252e3b');
    g.fillStyle = s;
    g.fillRect(x, 8, 120, h - 16);
    g.fillStyle = 'rgba(226, 232, 240, .14)';
    g.beginPath();
    g.moveTo(x + 8, 8);
    g.lineTo(x + 44, 8);
    g.lineTo(x + 18, h - 8);
    g.lineTo(x + 4, h - 8);
    g.closePath();
    g.fill();
    g.fillStyle = 'rgba(15, 20, 28, .35)';
    g.fillRect(x + 14, h - 38, 42, 5);
    g.fillRect(x + 66, h - 35, 36, 5);
    g.fillStyle = 'rgba(241, 245, 249, .22)';
    g.fillRect(x + 28, 13, 24, 2);
    g.fillRect(x + 76, 13, 24, 2);
    g.fillStyle = '#1e2530';
    g.fillRect(x + 59, 8, 2, h - 16);
  }
});
// reticolo diagrid acciaio/vetro piu' chiaro per le vele elicoidali
const DIAGRID = disegnata(128, 128, (g, w, h) => {
  g.fillStyle = 'rgba(72, 86, 108, 0.72)';
  g.fillRect(0, 0, w, h);
  const grad = g.createLinearGradient(0, 0, w, 0);
  grad.addColorStop(0, 'rgba(241, 245, 249, 0.24)');
  grad.addColorStop(0.5, 'rgba(203, 213, 225, 0.08)');
  grad.addColorStop(1, 'rgba(241, 245, 249, 0.20)');
  g.fillStyle = grad;
  g.fillRect(0, 0, w, h);
  g.strokeStyle = 'rgba(241, 245, 249, 0.94)';
  g.lineWidth = 2.4;
  g.beginPath();
  for (let i = -1; i <= 2; i++) {
    const ox = i * 64;
    g.moveTo(ox, 0); g.lineTo(ox + 64, h);
    g.moveTo(ox + 64, 0); g.lineTo(ox, h);
  }
  for (let y = 0; y <= h; y += 32) {
    g.moveTo(0, y); g.lineTo(w, y);
  }
  g.stroke();
});
DIAGRID.wrapS = DIAGRID.wrapT = THREE.RepeatWrapping;
// alone tondo bianco: si colora col materiale
const ALONE = disegnata(128, 128, (g, w) => {
  const r = g.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2);
  r.addColorStop(0, 'rgba(255, 255, 255, .9)');
  r.addColorStop(1, 'rgba(255, 255, 255, 0)');
  g.fillStyle = r;
  g.fillRect(0, 0, w, w);
});
// striscia che sfuma: forte in basso (v = 0), nulla in alto
const SFUMATA = disegnata(8, 128, (g, w, h) => {
  const s = g.createLinearGradient(0, 0, 0, h);
  s.addColorStop(0, 'rgba(255, 255, 255, 0)');
  s.addColorStop(1, 'rgba(255, 255, 255, .9)');
  g.fillStyle = s;
  g.fillRect(0, 0, w, h);
});
// ringhiera sottile: corrimano, piede e montanti (trasparente fra i montanti)
const RINGHIERA = disegnata(64, 64, (g) => {
  g.fillStyle = '#fff';
  g.fillRect(0, 0, 64, 5);
  g.fillRect(0, 58, 64, 4);
  for (let x = 2; x < 64; x += 16) g.fillRect(x, 0, 3, 64);
});
// interni: la citta' di notte fuori dalle vetrate
const NOTTURNO = disegnata(256, 128, (g, w, h) => {
  const s = g.createLinearGradient(0, 0, 0, h);
  s.addColorStop(0, '#05070f');
  s.addColorStop(1, '#16203e');
  g.fillStyle = s;
  g.fillRect(0, 0, w, h);
  for (let i = 0; i < 9; i++) {
    const x = i * 30 - 6, alto = 40 + caso(i) * 60;
    g.fillStyle = '#0b1022';
    g.fillRect(x, h - alto, 26, alto);
    for (let k = 0; k < 10; k++) {
      if (caso(i * 10 + k) > 0.3) continue;
      g.fillStyle = caso(k + i) > 0.5 ? '#ffcf8a' : '#7fe0ff';
      g.fillRect(x + 4 + (k % 3) * 7, h - alto + 6 + Math.floor(k / 3) * 9, 4, 4);
    }
  }
  g.fillStyle = NEON.rosa;
  g.fillRect(150, 52, 3, 30);
  g.fillStyle = '#121729';
  for (let x = 0; x < w; x += 64) g.fillRect(x, 0, 4, h);
  g.fillRect(0, 0, w, 4);
  g.fillRect(0, h - 4, w, 4);
});
NOTTURNO.wrapS = THREE.RepeatWrapping;
const MAT_VETRATE = new THREE.MeshBasicMaterial({ map: NOTTURNO });
// citta' lontana: poche finestre accese
const CITTA = disegnata(64, 128, (g, w, h) => {
  g.fillStyle = '#0e1218';
  g.fillRect(0, 0, w, h);
  for (let y = 6; y < h - 4; y += 9) {
    for (let x = 4; x < w - 4; x += 8) {
      const k = caso(x * 31 + y);
      if (k > 0.1) continue;
      g.fillStyle = k < 0.04 ? '#94a3b8' : '#cbd5e1';
      g.fillRect(x, y, 4, 4);
    }
  }
});
// codice sugli schermi delle scrivanie: righe colorate, scorrono piano
const CODICE = disegnata(128, 256, (g, w, h) => {
  g.fillStyle = '#0a1430';
  g.fillRect(0, 0, w, h);
  const colori = ['#7fe6ff', '#ff8fd8', '#e8f0ff', '#ffd27a', '#8ff0a8'];
  for (let y = 6, k = 0; y < h - 4; y += 9, k++) {
    let x = 8 + Math.floor(caso(k) * 3) * 10;
    while (x < w - 12) {
      const l = 8 + caso(k * 7 + x) * 26;
      g.fillStyle = colori[Math.floor(caso(k + x) * colori.length)];
      g.fillRect(x, y, Math.min(l, w - 8 - x), 4);
      x += l + 6;
      if (caso(x + k) > 0.6) break;
    }
  }
});
CODICE.wrapT = THREE.RepeatWrapping;
CODICE.repeat.set(1, 0.5);

function instanziato(geo, mat, n) {
  const m = new THREE.InstancedMesh(geo, mat, n);
  m.frustumCulled = false;
  return m;
}
const pezzo = (x, y, z, sx, sy, sz, angolo = 0) => new THREE.Matrix4().compose(new THREE.Vector3(x, y, z),
  new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), angolo), new THREE.Vector3(sx, sy, sz));

// ===== Robottini: un InstancedMesh per parte; il robot guarda +z (verso la camera) =====
// pochi segmenti: sul piano Engineering sono 168 robot, deve girare su un telefono medio
const GEO = {
  sedia: new THREE.CylinderGeometry(0.3, 0.22, 0.44, 8).translate(0, 0.22, 0),
  corpo: new THREE.CapsuleGeometry(0.24, 0.24, 3, 10).translate(0, 0.8, 0),
  testa: new THREE.SphereGeometry(0.4, 16, 12).translate(0, 1.55, 0),
  // visiera: una fascia della sfera davanti al viso
  visiera: new THREE.SphereGeometry(0.412, 14, 4, Math.PI / 2 - 1.0, 2.0, 1.12, 0.72).translate(0, 1.55, 0),
  occhio: new THREE.TorusGeometry(0.07, 0.022, 4, 10, Math.PI),  // un archetto "^": occhi che sorridono
  cuffia: new THREE.CylinderGeometry(0.15, 0.15, 0.1, 12).rotateZ(Math.PI / 2),
  archetto: new THREE.TorusGeometry(0.43, 0.04, 4, 14, Math.PI).translate(0, 1.55, 0),
  braccio: new THREE.CapsuleGeometry(0.075, 0.22, 2, 6).translate(0, -0.18, 0),  // perno alla spalla
};
const MAT = {
  sedia: new THREE.MeshStandardMaterial({ color: '#1c1f2b', roughness: 0.5 }),
  scrivania: new THREE.MeshStandardMaterial({ color: '#ece6da', roughness: 0.45 }),
  cassettiera: new THREE.MeshStandardMaterial({ color: '#cfc7b8', roughness: 0.5 }),
  cornice: new THREE.MeshStandardMaterial({ color: '#262c3d', roughness: 0.35, metalness: 0.4 }),
  schermo: new THREE.MeshBasicMaterial({ map: CODICE }),  // colore per istanza
  libri: new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.7 }),
  corpo: new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.35 }),  // colore del reparto per istanza
  // testa bianca lucida, con un po' di luce propria: resta bianca anche di notte
  testa: new THREE.MeshStandardMaterial({ color: '#f7f8fc', roughness: 0.18, metalness: 0.05, emissive: '#dfe6ff', emissiveIntensity: 0.28 }),
  visiera: new THREE.MeshStandardMaterial({ color: '#0b0e1a', roughness: 0.08, metalness: 0.7 }),
  cuffie: new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.35 }),  // colore del reparto per istanza
  luce: new THREE.MeshBasicMaterial({ color: '#ffffff' }),  // occhi, colore per istanza
};
const LIBRI = ['#e05a6d', '#4f8de0', '#f0c050', '#5bc28a'].map((c) => new THREE.Color(c));
// La postazione non si muove: [geometria, materiale, pezzi rispetto al robot seduto]
const ANGOLO_LATO = Math.atan2(0.82, -0.28);  // il monitor sull'ala sinistra guarda il robot (e la camera)
const POSTAZIONE = {
  sedia: [GEO.sedia, MAT.sedia, [new THREE.Matrix4()]],
  schienale: [CUBO, MAT.sedia, [pezzo(0, 0.78, -0.33, 0.56, 0.52, 0.08)]],
  piano: [CUBO, MAT.scrivania, [pezzo(0.05, 0.74, 0.72, 1.3, 0.06, 0.6), pezzo(-0.8, 0.74, 0.3, 0.42, 0.06, 0.95)]],
  cassettiera: [CUBO, MAT.cassettiera, [pezzo(0.5, 0.36, 0.72, 0.36, 0.68, 0.5)]],
  cornice: [CUBO, MAT.cornice, [pezzo(0.05, 1.04, 0.86, 0.66, 0.4, 0.04), pezzo(-0.82, 1.04, 0.28, 0.66, 0.4, 0.04, ANGOLO_LATO)]],
  // gli schermi guardano il robot: quello davanti gli da' le spalle alla camera, quello di lato si vede
  schermo: [PIANO_1, MAT.schermo, [pezzo(0.05, 1.04, 0.835, 0.6, 0.34, 1, Math.PI),
    pezzo(-0.82 + 0.025 * Math.sin(ANGOLO_LATO), 1.04, 0.28 + 0.025 * Math.cos(ANGOLO_LATO), 0.6, 0.34, 1, ANGOLO_LATO)]],
  libri: [CUBO, MAT.libri, [pezzo(0.42, 0.83, 0.55, 0.24, 0.12, 0.17)]],
};
// Il robot si muove (cammina, si gira, scrive): [geometria, materiale, quante per robot]
const PERSONA = {
  corpo: [GEO.corpo, MAT.corpo, 1], testa: [GEO.testa, MAT.testa, 1], visiera: [GEO.visiera, MAT.visiera, 1],
  archetto: [GEO.archetto, MAT.cuffie, 1], occhi: [GEO.occhio, MAT.luce, 2], cuffie: [GEO.cuffia, MAT.cuffie, 2],
  braccia: [GEO.braccio, MAT.corpo, 2],
};
const OCCHI = [-1, 1].map((l) => new THREE.Matrix4().makeTranslation(0.14 * l, 1.57, 0.4));
const CUFFIE = [-1, 1].map((l) => new THREE.Matrix4().makeTranslation(0.42 * l, 1.55, 0));
const SPALLE = [-1, 1].map((l) => new THREE.Vector3(0.29 * l, 1.04, 0.02));
const COLLO = 1.17;
const SEDUTO = [-1.1, -1.1];  // braccia avanti, sulla scrivania

let _gretaGLBPromise = null;
async function caricaGretaGLB(url) {
  if (!_gretaGLBPromise) {
    _gretaGLBPromise = (async () => {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buf = await res.arrayBuffer();
      const dv = new DataView(buf);
      if (dv.getUint32(0, true) !== 0x46546c67 || dv.getUint32(4, true) !== 2) throw new Error('GLB non valido');
      let off = 12, gltf = null, bin = null;
      while (off < buf.byteLength) {
        const len = dv.getUint32(off, true), typ = dv.getUint32(off + 4, true), start = off + 8;
        if (typ === 0x4e4f534a) gltf = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, start, len)));
        else if (typ === 0x004e4942) bin = buf.slice(start, start + len);
        off = start + len;
      }
      const TA = { 5120: Int8Array, 5121: Uint8Array, 5122: Int16Array, 5123: Uint16Array, 5125: Uint32Array, 5126: Float32Array };
      const NC = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 };
      const readAcc = (idx) => {
        const a = gltf.accessors[idx], bv = gltf.bufferViews[a.bufferView], T = TA[a.componentType], nc = NC[a.type];
        const off0 = (bv.byteOffset || 0) + (a.byteOffset || 0), stride = bv.byteStride || 0, elemBytes = T.BYTES_PER_ELEMENT * nc;
        if (!stride || stride === elemBytes) return new T(bin, off0, a.count * nc);
        const out = new T(a.count * nc), view = new DataView(bin);
        for (let i = 0; i < a.count; i++) {
          const base = off0 + i * stride;
          for (let c = 0; c < nc; c++) {
            const p = base + c * T.BYTES_PER_ELEMENT;
            if (T === Float32Array) out[i * nc + c] = view.getFloat32(p, true);
            else if (T === Uint32Array) out[i * nc + c] = view.getUint32(p, true);
            else if (T === Uint16Array) out[i * nc + c] = view.getUint16(p, true);
            else out[i * nc + c] = view.getUint8(p);
          }
        }
        return out;
      };
      const binBytes = new Uint8Array(bin);
      const loadTex = async (ti, srgb) => {
        if (!ti) return null;
        const td = gltf.textures?.[ti.index];
        const si = td?.extensions?.EXT_texture_webp?.source ?? td?.source;
        const im = si != null ? gltf.images?.[si] : null;
        if (!im || im.bufferView == null) return null;
        const bv = gltf.bufferViews[im.bufferView], s = bv.byteOffset || 0;
        const bmp = await createImageBitmap(new Blob([binBytes.subarray(s, s + bv.byteLength)], { type: im.mimeType || 'image/png' }));
        const t = new THREE.Texture(bmp);
        t.flipY = false;
        t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
        t.needsUpdate = true;
        return t;
      };
      const mats = await Promise.all((gltf.materials || [{}]).map(async (md) => {
        const p = md.pbrMetallicRoughness || {};
        const [map, mr, norm] = await Promise.all([
          loadTex(p.baseColorTexture, true), loadTex(p.metallicRoughnessTexture, false), loadTex(md.normalTexture, false),
        ]);
        return new THREE.MeshStandardMaterial({
          color: p.baseColorFactor ? new THREE.Color(...p.baseColorFactor.slice(0, 3)) : new THREE.Color(0xffffff),
          roughness: p.roughnessFactor ?? 1, metalness: p.metallicFactor ?? 0,
          map, roughnessMap: mr, metalnessMap: mr, normalMap: norm,
        });
      }));
      const grp = new THREE.Group();
      for (const md of gltf.meshes || []) {
        for (const pr of md.primitives || []) {
          const g = new THREE.BufferGeometry();
          if (pr.attributes.POSITION != null) g.setAttribute('position', new THREE.BufferAttribute(readAcc(pr.attributes.POSITION), 3));
          if (pr.attributes.NORMAL != null) g.setAttribute('normal', new THREE.BufferAttribute(readAcc(pr.attributes.NORMAL), 3));
          if (pr.attributes.TEXCOORD_0 != null) g.setAttribute('uv', new THREE.BufferAttribute(readAcc(pr.attributes.TEXCOORD_0), 2));
          if (pr.indices != null) g.setIndex(new THREE.BufferAttribute(readAcc(pr.indices), 1));
          const m = new THREE.Mesh(g, mats[pr.material ?? 0]);
          m.castShadow = true; m.receiveShadow = true;
          grp.add(m);
        }
      }
      return grp;
    })();
  }
  return (await _gretaGLBPromise).clone();
}

function robottini(p) {
  const n = p.agenti.length, gruppo = new THREE.Group(), r = { gruppo };
  for (const [parte, [geo, mat, pezzi]] of Object.entries(POSTAZIONE)) r[parte] = instanziato(geo, mat, n * pezzi.length);
  for (const [parte, [geo, mat, k]] of Object.entries(PERSONA)) r[parte] = instanziato(geo, mat, n * k);
  for (const [k, v] of Object.entries(r)) if (k !== 'gruppo') gruppo.add(v);
  const reparto = new THREE.Color(p.colore), cuffia = reparto.clone().lerp(new THREE.Color('#ffffff'), 0.15);
  const base = new THREE.Matrix4(), m = new THREE.Matrix4(), q = new THREE.Quaternion(), v = new THREE.Vector3();
  p.agenti.forEach((a, i) => {
    // fra le file la scrivania resta di misura normale anche per il comanager: i corridoi restano liberi
    base.compose(v.set(a.x, 0, a.z), q, new THREE.Vector3().setScalar(a.x < 0 ? a.scala : ROBOT));
    for (const [parte, [, , pezzi]] of Object.entries(POSTAZIONE)) {
      pezzi.forEach((pz, k) => r[parte].setMatrixAt(i * pezzi.length + k, m.multiplyMatrices(base, pz)));
    }
    posa(r, i, a.x, 0, a.z, 0, a.scala, undefined, false);
    r.corpo.setColorAt(i, reparto);
    r.archetto.setColorAt(i, cuffia);
    for (const k of [0, 1]) {
      r.cuffie.setColorAt(2 * i + k, cuffia);
      r.braccia.setColorAt(2 * i + k, reparto);
    }
    r.libri.setColorAt(i, LIBRI[i % LIBRI.length]);
    if (a.id === 'segretaria') {
      r.gretaIndex = i;
      r.gretaPos = { x: a.x, y: 0, z: a.z, angolo: 0, scala: a.scala };
      caricaGretaGLB('/greta-3d/models/greta-rodin-opt.glb').then((modello) => {
        if (gruppo.userData.smontato) return;
        const box = new THREE.Box3().setFromObject(modello);
        const h = Math.max(0.01, box.max.y - box.min.y);
        const baseScale = (1.85 * a.scala) / h;
        const wrap = new THREE.Group();
        modello.scale.setScalar(baseScale);
        modello.position.y = -box.min.y * baseScale;
        wrap.add(modello);
        r.gretaMesh = wrap;
        gruppo.add(wrap);
        const gp = r.gretaPos;
        posa(r, i, gp.x, gp.y, gp.z, gp.angolo, gp.scala, {}, true);
      }).catch(() => {});
    }
  });
  luciRobot(r, p, new Set(), new Set());
  return r;
}

// mette il robot i in (x, y, z) girato di `angolo`; cenno = testa in avanti, bracci = angoli delle braccia.
// Con `carica` segna da ricaricare solo le matrici di questa istanza.
const _m = new THREE.Matrix4(), _h = new THREE.Matrix4(), _r = new THREE.Matrix4(), _q = new THREE.Quaternion();
const _e = new THREE.Euler(), _v = new THREE.Vector3(), _s = new THREE.Vector3();
function posa(r, i, x, y, z, angolo, s, { rollio = 0, cenno = 0, bracci = SEDUTO } = {}, carica = true) {
  let scalaInst = s;
  if (r.gretaIndex === i) {
    r.gretaPos = { x, y, z, angolo, scala: s };
    if (r.gretaMesh) {
      r.gretaMesh.position.set(x, y, z);
      r.gretaMesh.rotation.set(0, angolo, rollio, 'YXZ');
      scalaInst = 0;
    }
  }
  _m.compose(_v.set(x, y, z), _q.setFromEuler(_e.set(0, angolo, rollio, 'YXZ')), _s.setScalar(scalaInst));
  r.corpo.setMatrixAt(i, _m);
  // testa: ruota attorno al collo
  _h.makeTranslation(0, COLLO, 0).multiply(_r.makeRotationX(cenno)).multiply(_r.makeTranslation(0, -COLLO, 0)).premultiply(_m);
  for (const parte of ['testa', 'visiera', 'archetto']) r[parte].setMatrixAt(i, _h);
  for (let k = 0; k < 2; k++) {
    r.occhi.setMatrixAt(2 * i + k, _r.multiplyMatrices(_h, OCCHI[k]));
    r.cuffie.setMatrixAt(2 * i + k, _r.multiplyMatrices(_h, CUFFIE[k]));
    _r.makeRotationX(bracci[k]).setPosition(SPALLE[k]);
    r.braccia.setMatrixAt(2 * i + k, _r.premultiply(_m));
  }
  if (!carica) return;
  for (const [parte, [, , k]] of Object.entries(PERSONA)) {
    r[parte].instanceMatrix.addUpdateRange(i * k * 16, k * 16);
    r[parte].instanceMatrix.needsUpdate = true;
  }
}

// occhi e schermi secondo lo stato; restituisce il tipo di ogni agente
function luciRobot(r, p, lav, coda) {
  const occhio = new THREE.Color(), schermo = new THREE.Color();
  const tipi = p.agenti.map((a, i) => {
    const tipo = lav.has(a.id) ? 'lavora' : coda.has(a.id) ? 'coda' : 'fermo';
    occhio.set(LUCI.occhi[tipo]);
    schermo.set(LUCI.schermo[tipo]);
    for (const k of [0, 1]) {
      r.occhi.setColorAt(2 * i + k, occhio);
      r.schermo.setColorAt(2 * i + k, schermo);
    }
    return tipo;
  });
  if (p.agenti.length) {  // instanceColor nasce al primo setColorAt
    r.occhi.instanceColor.needsUpdate = true;
    r.schermo.instanceColor.needsUpdate = true;
  }
  return tipi;
}

// ===== Segni dello stato: icona olografica, anello a terra, arco col freccia dal capo =====
// icona sopra chi lavora (barre) o e' in coda (clessidra): forma diversa, non solo colore
function iconaStato(colore, simbolo) {
  return disegnata(128, 128, (g) => {
    g.shadowColor = colore;
    g.shadowBlur = 16;
    g.lineWidth = 7;
    g.strokeStyle = colore;
    g.fillStyle = 'rgba(8, 14, 32, .72)';
    g.beginPath();
    g.arc(64, 64, 46, 0, Math.PI * 2);
    g.fill();
    g.stroke();
    g.fillStyle = colore;
    simbolo(g);
  });
}
const ICONA = {
  lavora: iconaStato(NEON.lavora, (g) => {
    for (const [x, h] of [[44, 26], [60, 44], [76, 32]]) g.fillRect(x - 5, 84 - h, 10, h);
  }),
  coda: iconaStato(NEON.coda, (g) => {
    g.beginPath();
    g.moveTo(44, 38); g.lineTo(84, 38); g.lineTo(64, 64); g.lineTo(84, 90); g.lineTo(44, 90); g.lineTo(64, 64);
    g.closePath();
    g.fill();
  }),
};
// dentro: sempre davanti a tutto, a misura fissa sullo schermo; fuori (anteprima e segnale del piano) nascoste dai muri
const OLOGRAMMA = { lavora: new THREE.SpriteMaterial({ map: ICONA.lavora, depthTest: false }),
  coda: new THREE.SpriteMaterial({ map: ICONA.coda, depthTest: false }) };
const SEGNALE = { lavora: new THREE.SpriteMaterial({ map: ICONA.lavora }), coda: new THREE.SpriteMaterial({ map: ICONA.coda }) };
const OLOGRAMMA_PX = 42;  // misura sullo schermo, a ogni zoom
const BOLLA_PX = 36;
// anello a terra: pieno per chi lavora, tratteggiato per chi e' in coda
function anello(colore, tratteggio) {
  const tex = disegnata(256, 256, (g) => {
    g.shadowColor = colore;
    g.shadowBlur = 18;
    g.strokeStyle = colore;
    g.lineWidth = 16;
    g.setLineDash(tratteggio);
    g.beginPath();
    g.arc(128, 128, 96, 0, Math.PI * 2);
    g.stroke();
  });
  return bagliore(tex, '#ffffff');
}
const ANELLO = { lavora: anello(NEON.lavora, []), coda: anello(NEON.coda, [26, 18]) };
const GEO_ANELLO = new THREE.PlaneGeometry(2.6, 2.6).rotateX(-Math.PI / 2);
const ARCO = Object.fromEntries(['lavora', 'coda'].map((k) => [k, new THREE.MeshBasicMaterial(
  { color: NEON[k], transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false })]));
const GEO_PUNTA = new THREE.ConeGeometry(0.3, 0.75, 8);
const ASSE_Y = new THREE.Vector3(0, 1, 0);

// arco al neon dal capo ad a, con la punta di freccia vicino ad a
function arco(capo, a, tipo) {
  const da = new THREE.Vector3(capo.x, 1.9 * capo.scala, capo.z), a1 = new THREE.Vector3(a.x, 1.95 * a.scala, a.z);
  const cima = da.clone().lerp(a1, 0.5).setY(Math.max(da.y, a1.y) + 2 + da.distanceTo(a1) * 0.12);
  const curva = new THREE.QuadraticBezierCurve3(da, cima, a1);
  const tubo = new THREE.Mesh(new THREE.TubeGeometry(curva, 32, 0.09, 5, false), ARCO[tipo]);
  tubo.userData.propria = true;
  const punta = new THREE.Mesh(GEO_PUNTA, ARCO[tipo]);
  punta.position.copy(curva.getPoint(0.95));
  punta.quaternion.setFromUnitVectors(ASSE_Y, curva.getTangent(0.95));
  return [tubo, punta];
}

// icone, anelli (e archi) di chi e' attivo; restituisce le icone (fluttuano nella vista piano)
function segniStato(gruppo, p, tipi, conArchi, materiali = OLOGRAMMA) {
  for (const o of gruppo.children) if (o.userData.propria) o.geometry.dispose();
  gruppo.clear();
  const capo = capoDi(p), icone = [];
  p.agenti.forEach((a, i) => {
    const tipo = tipi[i];
    if (tipo === 'fermo') return;
    const icona = new THREE.Sprite(materiali[tipo]);
    icona.center.set(0.5, 0);  // poggia sopra la testa
    icona.position.set(a.x, 2.05 * a.scala, a.z);
    icona.userData = { y: icona.position.y, fase: i };
    const terra = new THREE.Mesh(GEO_ANELLO, ANELLO[tipo]);
    terra.position.set(a.x, 0.05, a.z);
    terra.scale.setScalar(a.scala / ROBOT);
    gruppo.add(icona, terra);
    icone.push(icona);
    if (conArchi && capo && capo !== a) gruppo.add(...arco(capo, a, tipo));
  });
  return icone;
}

// ===== Vista esterna: palazzina di notte, i piani impilati; quello sotto il mouse esce come un cassetto =====
const esterno = new THREE.Group();
const flussiEsterni = new THREE.Group();
scena.add(esterno);
const cassetti = new Map();   // id piano -> { piano, gruppo, facciate, segnale, anteprima, robot, segni, y, t, da, a, inizio }
const colpibili = [];         // { box, piano | stanza }: scatole ferme per hover e clic
let boxEsterno = null;

function facciata(ripeti, scarto) {
  const t = ripetuta(FINESTRE, ripeti);
  t.offset.x = scarto;
  return new THREE.MeshStandardMaterial({ map: t, emissive: '#ffffff', emissiveMap: t, emissiveIntensity: 0.85,
    roughness: 0.2, metalness: 0.2 });
}
const NASCOSTA = new THREE.MeshBasicMaterial({ visible: false });

// ringhiera sottile lungo un lato del balcone
function ringhiera(mat, x0, x1, y, z0, z1) {
  const lungo = Math.max(x1 - x0, z1 - z0), m = new THREE.Mesh(PIANO_1, mat);
  m.scale.set(lungo, 1.05, 1);
  m.position.set((x0 + x1) / 2, y + 0.525, (z0 + z1) / 2);
  if (x1 - x0 < z1 - z0) m.rotation.y = Math.PI / 2;
  return m;
}

// silhouette rastremata per il corpo interno a cassetti, avvolto dalle vele elicoidali
function geometriaPiano(numero, maxPiano) {
  const { larghezza: W, profondita: D } = ESTERNO;
  const t = maxPiano > 1 ? Math.max(0, Math.min(1, (numero - 1) / (maxPiano - 1))) : 0;
  const scalaW = 0.98 - t * 0.32;
  const scalaD = 0.98 - t * 0.28;
  const w = Math.max(13.0, W * scalaW);
  const d = Math.max(10.0, D * scalaD);
  const offsetX = -t * 1.0;
  return { w, d, offsetX };
}

// 3 vele elicoidali a profondita' sfalsata (una primaria piu' in rilievo delle altre)
function creaVeleElicoidali(maxPiano, H, S) {
  const gruppo = new THREE.Group();
  const { larghezza: W, profondita: D } = ESTERNO;
  const texDiagrid = DIAGRID.clone();
  texDiagrid.needsUpdate = true;
  texDiagrid.repeat.set(3.5, 11);

  // [altezza, angoloBase, fattoreRaggio, ampiezza, opacita, colore, spessoreBordo]
  const profili = [
    [(maxPiano + 2.6) * H, 0.35, 1.15, 1.38, 0.86, '#e2e8f0', 0.22],
    [(maxPiano + 1.8) * H, 0.35 + (2 * Math.PI) / 3, 1.04, 1.22, 0.74, '#cbd5e1', 0.14],
    [(maxPiano + 1.1) * H, 0.35 + (4 * Math.PI) / 3, 0.96, 1.12, 0.64, '#94a3b8', 0.11],
  ];
  const NY = 30, NU = 8;

  for (const [hMax, a0, fR, spanBase, opacita, col, spessore] of profili) {
    const matVela = new THREE.MeshStandardMaterial({
      map: texDiagrid,
      color: col,
      transparent: true,
      opacity: opacita,
      roughness: 0.14,
      metalness: 0.82,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const matBordo = materiale(col, { roughness: 0.2, metalness: 0.9 });
    const pos = [], uvs = [], idx = [];
    const bordoSx = [], bordoDx = [];
    for (let iy = 0; iy <= NY; iy++) {
      const v = iy / NY;
      const y = S + v * (hMax - S);
      const cap = v > 0.78 ? Math.sqrt(Math.max(0.02, 1 - Math.pow((v - 0.78) / 0.22, 2))) : 1;
      const span = spanBase * (1 - 0.18 * v) * cap;
      const rx = (W * 0.58 * fR) * (1 - 0.32 * Math.pow(v, 1.1));
      const rz = (D * 0.60 * fR) * (1 - 0.28 * Math.pow(v, 1.1));
      const cx = -v * 1.0;
      for (let iu = 0; iu <= NU; iu++) {
        const u = iu / NU - 0.5;
        const theta = a0 + v * 1.9 + u * span;
        const x = cx + Math.cos(theta) * rx;
        const z = Math.sin(theta) * rz;
        pos.push(x, y, z);
        uvs.push(iu / NU, v);
        if (iu === 0) bordoSx.push(new THREE.Vector3(x, y, z));
        if (iu === NU) bordoDx.push(new THREE.Vector3(x, y, z));
      }
    }
    for (let iy = 0; iy < NY; iy++) {
      for (let iu = 0; iu < NU; iu++) {
        const a = iy * (NU + 1) + iu, b = a + 1, c = a + (NU + 1), d = c + 1;
        idx.push(a, b, c, b, d, c);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
    geo.setIndex(idx);
    geo.computeVertexNormals();
    gruppo.add(new THREE.Mesh(geo, matVela));

    for (const punti of [bordoSx, bordoDx]) {
      const curva = new THREE.CatmullRomCurve3(punti);
      gruppo.add(new THREE.Mesh(new THREE.TubeGeometry(curva, 28, spessore, 5, false), matBordo));
    }
  }
  return gruppo;
}

function costruisciEsterno() {
  const { larghezza: W, profondita: D, altezza: H, solaio: S, balcone: B } = ESTERNO;
  const maxPiano = Math.max(1, ...edificio.piani.map((p) => p.numero));
  const W0 = W + 3, D0 = D + 1.5;
  const x0Base = -W0 / 2, x1Base = W0 / 2, z0Base = -D0 / 2, z1Base = D0 / 2;
  const tetto = (maxPiano + 1) * H;
  const scuro = materiale('#2a3342', { roughness: 0.35, metalness: 0.55 });
  const acciaio = materiale('#546278', { roughness: 0.22, metalness: 0.85 });
  const filoPlain = '#cbd5e1';
  const insegnaPlain = '#f1f5f9';

  // piazza bagnata e lucida in ardesia scura, bordo plain argento
  const P = { x0: x0Base - 9, x1: x1Base + 9, z0: z0Base - 9, z1: z1Base + 9 + SCIVOLA };
  esterno.add(scatola(P.x0, P.x1, -0.8, 0, P.z0, P.z1, materiale('#111620', { roughness: 0.14, metalness: 0.72 })));
  const bordo = luce('#94a3b8'), riflessoBordo = bagliore(SFUMATA, '#cbd5e1', 0.28);
  const larghi = P.x1 - P.x0, lunghi = P.z1 - P.z0, cx = (P.x0 + P.x1) / 2, cz = (P.z0 + P.z1) / 2;
  esterno.add(
    scatola(P.x0, P.x1, 0, 0.08, P.z1 - 0.18, P.z1, bordo), scatola(P.x0, P.x1, 0, 0.08, P.z0, P.z0 + 0.18, bordo),
    scatola(P.x0, P.x0 + 0.18, 0, 0.08, P.z0, P.z1, bordo), scatola(P.x1 - 0.18, P.x1, 0, 0.08, P.z0, P.z1, bordo),
    aTerra(riflessoBordo, cx, 0.02, P.z1 - 1.7, larghi, 3, 0), aTerra(riflessoBordo, cx, 0.02, P.z0 + 1.7, larghi, 3, Math.PI),
    aTerra(riflessoBordo, P.x1 - 1.7, 0.02, cz, lunghi, 3, Math.PI / 2), aTerra(riflessoBordo, P.x0 + 1.7, 0.02, cz, lunghi, 3, -Math.PI / 2),
  );
  esterno.add(aTerra(bagliore(ALONE, '#e2e8f0', 0.22), 1, 0.015, 4, W0 + 18, D0 + 18));

  // piano terra: podio ovale/curvo chiaro con vetrine plain e padiglione d'ingresso a calice
  const terra = new THREE.Group();
  const basePodio = new THREE.Mesh(OVALE, scuro);
  basePodio.scale.set(W0, S, D0);
  basePodio.position.set(0, S / 2, 0);
  terra.add(basePodio);
  const ws = W0 / edificio.stanze.length;
  edificio.stanze.forEach((s, i) => {
    const sx = x0Base + i * ws, colore = COLORI_STANZE[i % 4];
    const vetrina = scatola(sx + 1.2, sx + ws - 1.2, S, S + 2.0, z0Base + 2.5, z1Base - 1.2, new THREE.MeshStandardMaterial(
      { color: '#2b3545', emissive: '#64748b', emissiveIntensity: 0.25, roughness: 0.2 }));
    terra.add(vetrina,
      insegna(s.nome, colore, sx + ws / 2, S + 2.85, z1Base - 1.15, ws - 1.4, 1.4));
    colpibili.push({ box: new THREE.Box3().setFromObject(vetrina), stanza: s.id });
  });
  const calice = new THREE.Mesh(new THREE.CylinderGeometry(2.2, 0.9, 3.6, 16, 1, false), acciaio);
  calice.position.set(x1Base - 2.6, 1.8, z1Base + 2.6);
  calice.rotation.z = -0.08;
  terra.add(calice);
  esterno.add(terra);

  // i piani: pianta ellittica/curva (non rettangolare!), colori plain chiari, cassetto invariato
  let topDim = { w: W0, d: D0, offsetX: 0 };
  for (const p of edificio.piani) {
    const { w: Wp, d: Dp, offsetX } = geometriaPiano(p.numero, maxPiano);
    const x0 = offsetX - Wp / 2, x1 = offsetX + Wp / 2, z0 = -Dp / 2, z1 = Dp / 2;
    const fw = Wp - B, fd = Dp - B;
    if (p.numero === maxPiano) topDim = { w: Wp, d: Dp, offsetX };

    const y = p.numero * H, gruppo = new THREE.Group();
    const matFacciata = facciata(3.2, (p.numero * 0.37) % 1);
    const solaioMesh = new THREE.Mesh(OVALE, scuro);
    solaioMesh.scale.set(Wp, S, Dp);
    solaioMesh.position.set(offsetX, y + S / 2, 0);

    const filoMesh = new THREE.Mesh(OVALE_APERTO, luce(filoPlain));
    filoMesh.scale.set(Wp + 0.08, 0.11, Dp + 0.08);
    filoMesh.position.set(offsetX, y + 0.2, 0);

    const vetriMesh = new THREE.Mesh(OVALE_APERTO, matFacciata);
    vetriMesh.scale.set(fw, H - S, fd);
    vetriMesh.position.set(offsetX, y + S + (H - S) / 2, 0);

    const corrimanoMesh = new THREE.Mesh(OVALE_APERTO, materiale('#94a3b8', { roughness: 0.28, metalness: 0.8 }));
    corrimanoMesh.scale.set(Wp - 0.15, 0.1, Dp - 0.15);
    corrimanoMesh.position.set(offsetX, y + S + 1.0, 0);

    gruppo.add(solaioMesh, filoMesh, vetriMesh, corrimanoMesh);

    const nome = insegna(p.nome, insegnaPlain, offsetX, y + S + 2.4, fd / 2 + 0.15, Math.min(16, Wp - 4), 2.5);
    gruppo.add(nome);
    const segnale = new THREE.Sprite(SEGNALE.lavora);
    segnale.center.set(0.5, 0);
    segnale.position.set(x1 - 1.2, y + S + 1.15, z1 * 0.65);
    segnale.scale.setScalar(3);
    segnale.visible = false;
    gruppo.add(segnale);
    esterno.add(gruppo);
    cassetti.set(p.id, {
      piano: p, gruppo, facciate: [matFacciata], nome: nome.material, segnale, anteprima: null, y: y + S,
      dim: { W: Wp, D: Dp, fx0: offsetX - fw / 2, fx1: offsetX + fw / 2, fz0: -fd / 2, fz1: fd / 2 },
      t: 0, da: 0, a: 0, inizio: 0,
    });
    colpibili.push({ box: new THREE.Box3(new THREE.Vector3(x0, y, z0), new THREE.Vector3(x1, y + H, z1)), piano: p.id });
  }

  // chiusura superiore ellittica + le 3 vele elicoidali a rilievo differenziato
  const sopra = tetto + S;
  const tettoMesh = new THREE.Mesh(OVALE, scuro);
  tettoMesh.scale.set(topDim.w, S, topDim.d);
  tettoMesh.position.set(topDim.offsetX, tetto + S / 2, 0);
  esterno.add(tettoMesh, creaVeleElicoidali(maxPiano, H, S));
  // auto low-poly parcheggiata in strada, in tono grafite/platino
  const auto = new THREE.Group(), carrozzeria = materiale('#1f2430', { roughness: 0.22, metalness: 0.78 });
  const ventola = materiale('#0f1218', { roughness: 0.6 });
  auto.add(scatola(-1.3, 1.3, 0.3, 0.85, -0.65, 0.65, carrozzeria),
    scatola(-0.6, 0.7, 0.85, 1.3, -0.58, 0.58, materiale('#0d1017', { roughness: 0.05, metalness: 0.9 })),
    scatola(1.28, 1.34, 0.55, 0.7, -0.55, -0.3, luce('#f1f5f9')), scatola(1.28, 1.34, 0.55, 0.7, 0.3, 0.55, luce('#f1f5f9')),
    scatola(-1.34, -1.28, 0.55, 0.7, -0.6, 0.6, luce('#94a3b8')),
    aTerra(bagliore(ALONE, NEON.rosa, 0.45), 0, 0.03, 0, 4, 2.4));
  for (const [x, z] of [[-0.8, -0.65], [0.8, -0.65], [-0.8, 0.65], [0.8, 0.65]]) {
    auto.add(mesh(CILINDRO, ventola, x, 0.32, z, [0.62, 0.22, 0.62]).rotateX(Math.PI / 2));
  }
  auto.position.set(x1Base + 4.5, 0, z1Base + 3);
  auto.rotation.y = -0.45;
  esterno.add(auto);
  // piante in vaso sulla piazza
  const posti = [[x0Base - 3, z1Base + 3], [x0Base - 3, z0Base - 3], [x1Base + 3, z0Base - 3], [x1Base + 7, z0Base + 4], [x0Base - 3, z1Base + 9], [x1Base + 6, z1Base + 10]];
  const vasi = instanziato(GEO_VASO, MAT_VASO, posti.length), foglie = instanziato(GEO_FOGLIE, MAT_FOGLIE, posti.length);
  const m = new THREE.Matrix4();
  posti.forEach(([x, z], i) => {
    vasi.setMatrixAt(i, m.makeScale(1.6, 1.6, 1.6).setPosition(x, 0, z));
    foglie.setMatrixAt(i, m.makeRotationY(i).scale(new THREE.Vector3(1.6, 1.8, 1.6)).setPosition(x, 0, z));
  });
  esterno.add(vasi, foglie);
  quartiere(P, [[x0Base - 1, x1Base + 1, z0Base - 1, z1Base + 1.4], [x1Base + 2, x1Base + 7, z1Base + 0.5, z1Base + 5.5],
    ...posti.map(([x, z]) => [x - 1, x + 1, z - 1, z + 1])]);

  // citta' lontana: sagome scure con poche finestre, sfumate dalla nebbia. In vista isometrica cio' che e' lontano
  // sale sullo schermo: le basi stanno sotto la piazza perche' l'orizzonte cada dietro l'edificio.
  const N = 54, citta = instanziato(CUBO, new THREE.MeshBasicMaterial({ map: CITTA, color: '#64748b' }), N);
  for (let i = 0; i < N; i++) {
    const d = 110 + (i % 3) * 50 + caso(i) * 25, t = (caso(i + 50) - 0.5) * 330;
    const alto = 14 + caso(i + 99) * 50, lato = 5 + caso(i + 7) * 7;
    const base = -(d / Math.sqrt(3) + 4) * Math.sqrt(6) / 2;
    m.compose(new THREE.Vector3((t - d) / Math.SQRT2, base + alto / 2, (-t - d) / Math.SQRT2), new THREE.Quaternion(),
      new THREE.Vector3(lato, alto, lato));
    citta.setMatrixAt(i, m);
  }
  esterno.add(citta, flussiEsterni);
  const yMin = Math.min(-0.8, ...edificio.piani.map((p) => p.numero * H));
  boxEsterno = new THREE.Box3(new THREE.Vector3(x0Base - 3, yMin, z0Base - 3), new THREE.Vector3(x1Base + 4.5, sopra + 16, z1Base + SCIVOLA));
}

// ===== Flussi luminosi verticali tra piani (deleghe, staffette, riunioni) =====
const matFascio = new Map();
let flussiAnimati = [];  // { sfere, yDa, yA }

function materialeFascio(colore) {
  if (!matFascio.has(colore)) {
    matFascio.set(colore, new THREE.MeshBasicMaterial({
      color: colore, transparent: true, opacity: 0.28, blending: THREE.AdditiveBlending, depthWrite: false,
    }));
  }
  return matFascio.get(colore);
}

function aggiornaFlussiEsterni() {
  flussiEsterni.clear();
  flussiAnimati = [];
  const lista = Array.isArray(stato.flussi) ? stato.flussi : [];
  if (!edificio || !lista.length) return;
  const { larghezza: W, profondita: D, altezza: H } = ESTERNO;
  const xBase = W / 2 + 0.85;
  const zBase = D / 2 - 2.6;
  lista.slice(0, 6).forEach((f, idx) => {
    const pDa = edificio.piani.find((p) => p.id === f.da_piano);
    const pA = edificio.piani.find((p) => p.id === f.a_piano);
    if (!pDa || !pA) return;
    const yDa = pDa.numero * H + H * 0.5;
    const yA = pA.numero * H + H * 0.5;
    const x = xBase + (idx % 2) * 0.55;
    const z = zBase - Math.floor(idx / 2) * 1.1;
    const colDa = pDa.colore || NEON.ciano;
    const colA = pA.colore || NEON.lavora;

    // terminali e braccetti verso i due piani
    flussiEsterni.add(
      mesh(SFERA, luce(colDa), x, yDa, z, 0.42),
      mesh(SFERA, luce(colA), x, yA, z, 0.48),
      scatola(W / 2 - 0.1, x, yDa - 0.06, yDa + 0.06, z - 0.06, z + 0.06, luce(colDa)),
      scatola(W / 2 - 0.1, x, yA - 0.06, yA + 0.06, z - 0.06, z + 0.06, luce(colA)),
    );
    const h = Math.abs(yA - yDa);
    if (h < 0.2) return;
    const ym = (yDa + yA) / 2;
    flussiEsterni.add(
      mesh(CILINDRO, luce(colA), x, ym, z, [0.16, h, 0.16]),
      mesh(CILINDRO, materialeFascio(colA), x, ym, z, [0.48, h, 0.48]),
    );
    const sfere = [0, 1, 2].map((k) => {
      const s = mesh(SFERA, luce('#ffffff'), x, yDa + (yA - yDa) * ((k + 1) / 4), z, 0.34);
      flussiEsterni.add(s);
      return s;
    });
    flussiAnimati.push({ sfere, yDa, yA });
  });
}

function muoviFlussiEsterni(t) {
  for (const fl of flussiAnimati) {
    fl.sfere.forEach((s, k) => {
      const frac = (t * 0.65 + k / fl.sfere.length) % 1;
      s.position.y = fl.yDa + (fl.yA - fl.yDa) * frac;
    });
  }
}

// ===== Quartiere: strade, chioschi e passanti attorno alla piazza =====
// Strade solo davanti e a destra (i lati che si vedono), con l'incrocio sull'angolo: dietro coprirebbero la citta'
// lontana. Restano fuori da boxEsterno, cosi' l'inquadratura resta sull'edificio.
const STRADA = 9, MARCIAPIEDE = 6, N_PASSANTI = 16, SCALA_PASSANTI = 1.4;
const GEO_POZZA = new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2);
const TENDA = disegnata(64, 8, (g, w, h) => {  // tenda a righe: il colore lo mette il materiale
  for (let i = 0; i < 8; i++) { g.fillStyle = i % 2 ? '#ffffff' : '#ffffff66'; g.fillRect(i * 8, 0, 8, h); }
});
let passanti = null;  // { r: InstancedMesh per parte (come i robottini), gente: stato di ognuno }

function quartiere(P, ostacoli) {
  const sx = P.x1 + STRADA, sz = P.z1 + STRADA, ex = sx + MARCIAPIEDE, ez = sz + MARCIAPIEDE;
  const marciapiede = materiale('#171c2c', { roughness: 0.5, metalness: 0.4 }), cordolo = luce('#34456e');
  esterno.add(
    scatola(P.x0, ex, -0.8, -0.3, P.z0, ez, materiale('#0c0e16', { roughness: 0.3, metalness: 0.55 })),  // asfalto
    scatola(P.x0, P.x1, -0.8, -0.1, sz, ez, marciapiede), scatola(sx, ex, -0.8, -0.1, P.z0, P.z1, marciapiede),
    scatola(sx, ex, -0.8, -0.1, sz, ez, marciapiede),
    scatola(P.x0, P.x1, -0.1, -0.05, sz, sz + 0.15, cordolo), scatola(sx, sx + 0.15, -0.1, -0.05, P.z0, P.z1, cordolo),
    scatola(sx, ex, -0.1, -0.05, sz, sz + 0.15, cordolo), scatola(sx, sx + 0.15, -0.1, -0.05, sz, ez, cordolo),
  );

  // segnaletica in un solo InstancedMesh: mezzerie gialle tratteggiate, strisce pedonali sui 4 lati dell'incrocio
  const segni = [], giallo = new THREE.Color('#d9b45a'), bianco = new THREE.Color('#b8bfd2');
  const mx = P.x1 + STRADA / 2, mz = P.z1 + STRADA / 2;
  for (let x = P.x0 + 2; x < P.x1 - 3; x += 3.2) segni.push([x, mz, 1.7, 0.2, giallo]);
  for (let z = P.z0 + 2; z < P.z1 - 3; z += 3.2) segni.push([mx, z, 0.2, 1.7, giallo]);
  segni.push([sx + 3.5, mz, 1.7, 0.2, giallo], [mx, sz + 3.5, 0.2, 1.7, giallo]);
  for (let k = 0.9; k < STRADA - 0.4; k += 1.05) {
    segni.push([P.x1 - 1.6, P.z1 + k, 2.2, 0.55, bianco], [sx + 1.6, P.z1 + k, 2.2, 0.55, bianco],
      [P.x1 + k, P.z1 - 1.6, 0.55, 2.2, bianco], [P.x1 + k, sz + 1.6, 0.55, 2.2, bianco]);
  }
  const strisce = instanziato(CUBO, new THREE.MeshBasicMaterial(), segni.length), m = new THREE.Matrix4();
  segni.forEach(([x, z, lx, lz, c], i) => {
    strisce.setMatrixAt(i, m.makeScale(lx, 0.02, lz).setPosition(x, -0.29, z));
    strisce.setColorAt(i, c);
  });

  // lampioni sui marciapiedi di fronte, con la pozza di luce calda per terra
  const lampioni = [[P.x0 + 6, sz + 1], [-4, sz + 1], [P.x1 - 6, sz + 1], [sx + 1, P.z0 + 8], [sx + 1, P.z1 - 10], [sx + 1.2, sz + 1.2]];
  const pali = instanziato(CILINDRO, materiale('#2a3044', { roughness: 0.4, metalness: 0.7 }), lampioni.length);
  const teste = instanziato(SFERA, luce('#ffd9a0'), lampioni.length);
  const pozze = instanziato(GEO_POZZA, bagliore(ALONE, '#ffb870', 0.5), lampioni.length);
  lampioni.forEach(([x, z], i) => {
    pali.setMatrixAt(i, m.makeScale(0.18, 5, 0.18).setPosition(x, 2.4, z));
    teste.setMatrixAt(i, m.makeScale(0.6, 0.6, 0.6).setPosition(x, 4.95, z));
    pozze.setMatrixAt(i, m.makeScale(7, 1, 7).setPosition(x, -0.07, z));
  });
  esterno.add(strisce, pali, teste, pozze);

  // chioschi del cibo sulla piazza: davanti guardano la strada (e la camera), quello a destra guarda +x
  for (const [testo, colore, x, z, angolo] of [['HOT DOG', '#ff5f6d', -8, P.z1 - 3.4, 0],
    ['RAMEN', '#ffb347', 6, P.z1 - 3.4, 0], ['PIZZA', '#6ff0a0', P.x1 - 3.6, 2, Math.PI / 2]]) {
    esterno.add(chiosco(testo, colore, x, z, angolo));
    ostacoli.push(angolo ? [x - 1.6, x + 2.6, z - 2.2, z + 2.2] : [x - 2.2, x + 2.2, z - 1.6, z + 2.6]);
  }

  // passanti: le stesse parti dei robottini, un InstancedMesh per parte (7 draw call per tutti)
  const r = { gruppo: new THREE.Group() };
  for (const [parte, [geo, mat, k]] of Object.entries(PERSONA)) r.gruppo.add(r[parte] = instanziato(geo, mat, N_PASSANTI * k));
  const zone = [
    { x0: P.x0 + 1.2, x1: P.x1 - 1.2, z0: P.z0 + 1.2, z1: P.z1 - 1.2, y: 0, ostacoli },  // piazza
    { x0: P.x0 + 1, x1: P.x1 - 1, z0: sz + 1.9, z1: ez - 1.2, y: -0.1, ostacoli: [] },    // marciapiede davanti
    { x0: sx + 1.9, x1: ex - 1.2, z0: P.z0 + 1, z1: P.z1 - 1, y: -0.1, ostacoli: [] },    // marciapiede a destra
  ];
  const tinte = [...COLORI_STANZE, NEON.rosa, NEON.ciano, '#c084fc', '#e8ecf5'].map((c) => new THREE.Color(c));
  const occhio = new THREE.Color('#7fe9ff'), chiaro = new THREE.Color('#ffffff');
  const gente = Array.from({ length: N_PASSANTI }, (_, i) => {
    const c = tinte[i % tinte.length], cuffia = c.clone().lerp(chiaro, 0.3);
    r.corpo.setColorAt(i, c);
    r.archetto.setColorAt(i, cuffia);
    for (const k of [0, 1]) {
      r.braccia.setColorAt(2 * i + k, c);
      r.cuffie.setColorAt(2 * i + k, cuffia);
      r.occhi.setColorAt(2 * i + k, occhio);
    }
    const zona = zone[i < 11 ? 0 : i < 14 ? 1 : 2], [x, z] = puntoLibero(zona), angolo = Math.random() * 2 * Math.PI;
    return { zona, x, z, tx: x, tz: z, angolo, verso: angolo, pausa: Math.random() * 3, vel: 1.6 + Math.random() * 1.2,
      fase: Math.random() * 10 };
  });
  esterno.add(r.gruppo);
  passanti = { r, gente };
  muoviPassanti(0, 0);  // con "riduci movimento" restano cosi', fermi
}

function chiosco(testo, colore, x, z, angolo) {
  const g = new THREE.Group(), scuro = materiale('#141826', { roughness: 0.5 });
  const corpo = materiale(mescola(colore, '#10131f', 0.6), { roughness: 0.4, metalness: 0.3 });
  g.add(scatola(-1.5, 1.5, 0, 1.1, -1, 0.9, corpo),
    scatola(-1.55, 1.55, 1.1, 1.18, -1.05, 1.0, luce(colore)),                                   // bancone, bordo acceso
    scatola(-1.5, 1.5, 1.18, 2.5, -1, -0.7, scuro),                                              // retro
    scatola(-1.5, -1.38, 1.18, 2.5, 0.6, 0.72, scuro), scatola(1.38, 1.5, 1.18, 2.5, 0.6, 0.72, scuro),
    scatola(-1.8, 1.8, 2.5, 2.68, -1.1, 1.5, new THREE.MeshBasicMaterial({ map: TENDA, color: colore })),  // tenda
    insegna(testo, colore, 0, 3.2, -0.6, 2.8, 0.9),
    mesh(CILINDRO, scuro, -0.8, 0.35, 1.6, [0.45, 0.7, 0.45]), mesh(CILINDRO, scuro, 0.8, 0.35, 1.6, [0.45, 0.7, 0.45]),
    aTerra(bagliore(ALONE, colore, 0.5), 0, 0.03, 1.4, 6, 4.5));
  g.position.set(x, 0, z);
  g.rotation.y = angolo;
  return g;
}

const fuoriOstacolo = (zona, x, z) => !zona.ostacoli.some(([a, b, c, d]) => x > a && x < b && z > c && z < d);
function puntoLibero(zona) {
  for (let k = 0; k < 50; k++) {
    const x = zona.x0 + Math.random() * (zona.x1 - zona.x0), z = zona.z0 + Math.random() * (zona.z1 - zona.z0);
    if (fuoriOstacolo(zona, x, z)) return [x, z];
  }
  return [zona.x0, zona.z0];  // angolo della zona: sempre libero
}
// prossima meta: un punto a caso della sua zona raggiungibile in linea retta senza passare dagli ostacoli
function nuovaMeta(w) {
  for (let k = 0; k < 10; k++) {
    const [tx, tz] = puntoLibero(w.zona), n = Math.ceil(Math.hypot(tx - w.x, tz - w.z) / 0.5);
    let libero = true;
    for (let j = 1; j <= n && libero; j++) libero = fuoriOstacolo(w.zona, w.x + (tx - w.x) * j / n, w.z + (tz - w.z) * j / n);
    if (libero) return Object.assign(w, { tx, tz });
  }
  w.pausa = 0.5;  // niente strada libera: riprova fra poco
}

function muoviPassanti(dt, t) {
  const { r, gente } = passanti;
  gente.forEach((w, i) => {
    let passi = false;
    if (w.pausa > 0) {
      w.pausa -= dt;
      if (w.pausa <= 0) nuovaMeta(w);
    } else {
      const dx = w.tx - w.x, dz = w.tz - w.z, d = Math.hypot(dx, dz), passo = w.vel * dt;
      if (d <= passo) Object.assign(w, { x: w.tx, z: w.tz, pausa: 0.8 + Math.random() * 3 });
      else {
        w.x += dx / d * passo;
        w.z += dz / d * passo;
        w.verso = Math.atan2(dx, dz);
        passi = true;
      }
    }
    w.angolo = angoloVerso(w.angolo, w.verso, 1 - Math.exp(-8 * dt));
    const ritmo = Math.sin(t * 8 + w.fase);
    posa(r, i, w.x, w.zona.y + (passi ? Math.abs(ritmo) * 0.08 : 0), w.z, w.angolo, SCALA_PASSANTI, passi
      ? { rollio: ritmo * 0.07, bracci: [ritmo * 0.5, -ritmo * 0.5] }
      : { cenno: Math.sin(t * 1.5 + w.fase) * 0.05, bracci: [0, 0] }, false);
  });
  for (const parte of Object.keys(PERSONA)) r[parte].instanceMatrix.needsUpdate = true;
}

// anteprima del piano uscito: gli stessi robottini in miniatura, centrati nel piano, con lo stato
function anteprima(c) {
  const { larghezza: Wdef, profondita: Ddef, balcone: B } = ESTERNO, p = c.piano, g = new THREE.Group();
  const W = c.dim?.W ?? Wdef, D = c.dim?.D ?? Ddef;
  const cx = c.dim ? (c.dim.fx0 + c.dim.fx1) / 2 : -B / 2;
  const cz = c.dim ? (c.dim.fz0 + c.dim.fz1) / 2 : -B / 2 + 0.3;
  if (p.agenti.length) {
    const xs = p.agenti.map((a) => a.x), zs = p.agenti.map((a) => a.z);
    const ax0 = Math.min(...xs) - 2, ax1 = Math.max(...xs) + 2, az0 = Math.min(...zs) - 1.5, az1 = Math.max(...zs) + 2.5;
    const s = Math.min((W - B - 2) / (ax1 - ax0), (D - B - 2) / (az1 - az0), 0.8);
    g.scale.setScalar(s);
    g.position.set(cx - (ax0 + ax1) / 2 * s, c.y, cz - (az0 + az1) / 2 * s);
    c.robot = robottini(p);
    c.segni = new THREE.Group();
    c.scala = s;
    // pavimento chiaro sotto i robottini, cosi' si leggono anche dietro i vetri
    g.add(scatola(ax0, ax1, 0.01, 0.05, az0, az1, materiale(mescola(p.colore, '#2a3040', 0.75))), c.robot.gruppo, c.segni);
  }
  g.visible = false;
  c.gruppo.add(g);
  c.anteprima = g;
  statoFuori(c, insiemi());
}

// segnale sul balcone e (se c'e') anteprima con occhi, schermi, icone e anelli aggiornati
function statoFuori(c, { lav, coda }) {
  const ids = c.piano.agenti.map((a) => a.id);
  const tipo = ids.some((id) => lav.has(id)) ? 'lavora' : ids.some((id) => coda.has(id)) ? 'coda' : null;
  c.segnale.visible = !!tipo;
  if (tipo) c.segnale.material = SEGNALE[tipo];
  if (!c.robot) return;
  const icone = segniStato(c.segni, c.piano, luciRobot(c.robot, c.piano, lav, coda), false, SEGNALE);
  for (const o of icone) o.scale.setScalar(2.4 / c.scala);  // circa 2.4 unita' nel mondo
  for (const o of c.segni.children) if (o.isMesh) o.scale.multiplyScalar(2);
}

// fa uscire solo il piano `id` (null: tutti dentro); gli altri non si muovono
function estrai(id) {
  if (pianoAperto || !cassetti.size) id = null;
  if (id === estratto) return;
  const ora = performance.now();
  for (const [pid, c] of cassetti) {
    const a = pid === id ? 1 : 0;
    if (c.a !== a) Object.assign(c, { da: c.t, a, inizio: ora });
  }
  if (id && !cassetti.get(id).anteprima) anteprima(cassetti.get(id));
  estratto = id;
  aggiornaHud();
  richiediRender();
}

// avanza le animazioni dei cassetti; true finche' qualcuno sta ancora scivolando
function muoviCassetti(ora) {
  let ancora = false;
  for (const c of cassetti.values()) {
    if (c.t === c.a) continue;
    const k = DURATA ? Math.min(1, Math.max(0, (ora - c.inizio) / DURATA)) : 1;
    c.t = k === 1 ? c.a : c.da + (c.a - c.da) * (1 - (1 - k) ** 3);  // rallenta alla fine
    applicaCassetto(c);
    if (k < 1) ancora = true;
  }
  return ancora;
}

function applicaCassetto(c) {
  c.gruppo.position.z = c.t * SCIVOLA;
  const vetro = c.t > 0;  // mentre esce le vetrate si schiariscono e si vede dentro
  for (const m of c.facciate) {
    if (m.transparent !== vetro) Object.assign(m, { transparent: vetro, depthWrite: !vetro, needsUpdate: true });
    m.opacity = 1 - 0.84 * c.t;
  }
  c.nome.opacity = 1 - 0.85 * c.t;  // anche l'insegna si schiarisce, se no copre l'interno
  if (c.anteprima) c.anteprima.visible = vetro;
}

// tutti dentro subito, senza animazione (si entra in un piano)
function chiudiCassetti() {
  for (const c of cassetti.values()) {
    Object.assign(c, { t: 0, da: 0, a: 0 });
    applicaCassetto(c);
  }
  estratto = null;
}

const puntoColpito = new THREE.Vector3(), boxSpostata = new THREE.Box3();
// il piu' vicino fra le scatole ferme e quella del piano uscito, dove si trova adesso
function colpitoFuori() {
  let meglio = null, dMin = Infinity;
  const prova = (box, cosa) => {
    if (!raggio.ray.intersectBox(box, puntoColpito)) return;
    const d = puntoColpito.distanceToSquared(raggio.ray.origin);
    if (d < dMin) { dMin = d; meglio = cosa; }
  };
  for (const c of colpibili) prova(c.box, c);
  if (estratto) {
    const c = cassetti.get(estratto), ferma = colpibili.find((x) => x.piano === estratto);
    prova(boxSpostata.copy(ferma.box).translate(new THREE.Vector3(0, 0, c.gruppo.position.z)), ferma);
  }
  return meglio && (meglio.piano ? { piano: meglio.piano } : { stanza: meglio.stanza });
}

// ===== Tabellone dei piani: come la targa all'ingresso, un piano per riga dall'alto; sotto il piano uscito si apre =====
const hud = $('hud');
const elencoPiani = el('ol', 'piani'), piede = el('p', 'piede'), piedeStats = el('p', 'piede stats'), dettaglio = el('div', 'dettaglio');
elencoPiani.setAttribute('aria-label', 'Piani');
dettaglio.setAttribute('aria-live', 'polite');
piedeStats.hidden = true;
hud.append(elencoPiani, piede, piedeStats);
const righe = new Map();  // id piano -> { li, spia }
let firmaHud = '';

// le righe si costruiscono una volta sola: rifarle sotto il cursore farebbe perdere hover e fuoco
function costruisciTabellone() {
  for (const p of [...edificio.piani].sort((a, b) => b.numero - a.numero)) {
    const li = el('li', 'piano'), spia = el('span', 'spia');
    li.style.setProperty('--piano', p.colore);
    const riga = bottone('', 'riga', (e) => {
      // mouse e tastiera entrano subito (il piano e' gia' uscito); al tocco il primo tocco lo fa uscire, il secondo entra
      if (ultimoPuntatore === 'mouse' || e.detail === 0 || estratto === p.id) apriPiano(p.id);
      else estrai(p.id);
    });
    riga.append(el('span', 'num', String(p.numero)), el('span', 'nome', p.nome), spia);
    // solo il fuoco da tastiera: al tocco il fuoco arriva prima del clic e farebbe entrare al primo tocco
    riga.addEventListener('focus', () => { if (riga.matches(':focus-visible')) estrai(p.id); });
    riga.addEventListener('pointerdown', (e) => usaPuntatore(e.pointerType));
    li.addEventListener('pointerenter', (e) => { if (e.pointerType === 'mouse') estrai(p.id); });
    li.append(riga);
    elencoPiani.append(li);
    righe.set(p.id, { li, spia });
  }
  hud.addEventListener('pointerleave', (e) => { if (e.pointerType === 'mouse') estrai(null); });
}

function aggiornaHud() {
  if (!edificio || pianoAperto) return;
  if (!righe.size) costruisciTabellone();
  const firma = JSON.stringify([estratto, stato]);
  if (firma === firmaHud) return;
  firmaHud = firma;
  const { lav, coda } = insiemi();

  const riepilogo = [`${agentiPerId.size} agenti`];
  if (lav.size) riepilogo.push(`${lav.size} al lavoro`);
  if (coda.size) riepilogo.push(`${coda.size} in coda`);
  if (riepilogo.length === 1) riepilogo.push('nessuno al lavoro');
  $('sottotitolo').textContent = riepilogo.join(', ');

  for (const piano of edificio.piani) {
    const { li, spia } = righe.get(piano.id);
    const quanti = (s) => piano.agenti.filter((a) => s.has(a.id)).length, l = quanti(lav), c = quanti(coda);
    li.classList.toggle('aperto', piano.id === estratto);
    spia.className = `spia ${l ? 'lavora' : c ? 'coda' : ''}`;
    spia.textContent = l || c || '';
    spia.title = l ? `${l} al lavoro` : c ? `${c} in coda` : '';
  }

  const ram = stato.ram_mb ? `${(stato.ram_mb / 1024).toLocaleString('it', { maximumFractionDigits: 1 })} GB` : '—';
  piede.textContent = `RAM libera del Pi: ${ram}` + (stato.attesa_ram ? '. Gli agenti aspettano che se ne liberi.' : '');
  piede.classList.toggle('attesa', !!stato.attesa_ram);

  const stats = stato.statistiche;
  if (stats && typeof stats.totale_agenti === 'number') {
    const c = stats.totale_claude || 0, g = stats.totale_gemini || 0, err = stats.totale_errori || 0;
    const tot = c + g;
    piedeStats.textContent = tot > 0
      ? `Chiamate: ${c} Claude · ${g} Gemini${err ? ` · ${err} err.` : ''} · ${stats.mai_usati ?? 0}/${stats.totale_agenti} mai usati`
      : `Nessuna chiamata registrata (${stats.totale_agenti} agenti).`;
    piedeStats.hidden = false;
  } else {
    piedeStats.hidden = true;
  }

  const p = edificio.piani.find((x) => x.id === estratto);
  if (!p) return dettaglio.remove();
  const capo = el('p', 'capo', 'Capo ');
  capo.append(el('strong', '', capoDi(p)?.nome || '—'), document.createTextNode(`, ${p.agenti.length} agenti`));
  const parti = [capo];
  const statPiano = (stats?.top_piani || []).find((x) => x.id === p.id && x.totale > 0);
  if (statPiano) {
    const rigaStat = el('p', 'capo');
    rigaStat.append('Chiamate: ', el('strong', '', String(statPiano.totale)),
      document.createTextNode(` (${statPiano.claude} Claude · ${statPiano.gemini} Gemini)`));
    parti.push(rigaStat);
  }
  const team = new Map();
  for (const a of p.agenti) if (a.team) team.set(a.team, (team.get(a.team) || 0) + 1);
  if (team.size) {
    const ordinati = [...team].sort((a, b) => b[1] - a[1]), max = ordinati[0][1], ul = el('ul', 'team');
    ul.setAttribute('aria-label', 'Team');
    for (const [nome, n] of ordinati) {
      const li = el('li');
      li.style.setProperty('--quota', n / max);
      li.append(el('span', '', nome), el('span', 'n', String(n)));
      ul.append(li);
    }
    parti.push(ul);
  }
  for (const [classe, titolo, insieme] of [['lavora', 'Al lavoro', lav], ['coda', 'In coda', coda]]) {
    const nomi = p.agenti.filter((a) => insieme.has(a.id)).map((a) => a.nome);
    if (!nomi.length) continue;
    const altri = nomi.length > 5 ? ` e altri ${nomi.length - 5}` : '';
    const riga = el('p', `attivi ${classe}`);
    riga.append(el('strong', '', `${titolo}: `), document.createTextNode(nomi.slice(0, 5).join(', ') + altri));
    parti.push(riga);
  }
  parti.push(bottone(`Entra in ${p.nome}`, 'entra', () => apriPiano(p.id)));
  dettaglio.replaceChildren(...parti);
  const { li } = righe.get(p.id);
  if (dettaglio.parentNode !== li) li.append(dettaglio);
  li.scrollIntoView({ block: 'nearest' });
}

// ===== Vista piano =====
function scalaOlogrammi() {
  const unita = (camera.top - camera.bottom) / camera.zoom / (contenitore.clientHeight || 1);
  for (const o of pianoAperto.attivi.children) if (o.isSprite) o.scale.set(OLOGRAMMA_PX * unita, OLOGRAMMA_PX * unita, 1);
  for (const o of pianoAperto.chiacchiere.children) o.scale.set(BOLLA_PX * unita, BOLLA_PX * unita, 1);
}

// maxischermo a parete: monitor leggibile con i dati reali del piano (lavorando, in coda, flussi, evento/commessa)
let schermoCanvas = null, schermoCtx = null, schermoTex = null, schermo = null, firmaSchermo = '';

function preparaSchermo() {
  if (schermo) return;
  schermoCanvas = document.createElement('canvas');
  schermoCanvas.width = 1024;
  schermoCanvas.height = 384;
  schermoCtx = schermoCanvas.getContext('2d');
  schermoTex = new THREE.CanvasTexture(schermoCanvas);
  schermoTex.colorSpace = THREE.SRGBColorSpace;
  schermoTex.anisotropy = 4;
  schermo = new THREE.MeshBasicMaterial({ map: schermoTex });
}

function righeTesto(g, testo, maxW, maxRighe) {
  const parole = String(testo || '').split(/\s+/).filter(Boolean);
  if (!parole.length) return [];
  const righe = [];
  let riga = '';
  for (const p of parole) {
    const prova = riga ? `${riga} ${p}` : p;
    if (g.measureText(prova).width <= maxW) {
      riga = prova;
    } else {
      if (riga) righe.push(riga);
      riga = p;
      if (righe.length >= maxRighe) break;
    }
  }
  if (riga && righe.length < maxRighe) righe.push(riga);
  if (righe.length === maxRighe && parole.join(' ').length > righe.join(' ').length) {
    righe[maxRighe - 1] = righe[maxRighe - 1].slice(0, -1) + '…';
  }
  return righe;
}

function aggiornaSchermo(p, ins = insiemi()) {
  if (!p) return;
  preparaSchermo();
  const lavNomi = p.agenti.filter((a) => ins.lav.has(a.id)).map((a) => a.nome);
  const codaNomi = p.agenti.filter((a) => ins.coda.has(a.id)).map((a) => a.nome);
  const flussiPiano = (stato.flussi || []).filter((f) => f.da_piano === p.id || f.a_piano === p.id);
  const evento = stato.eventi?.[p.id] || null;
  const firma = JSON.stringify([p.id, lavNomi, codaNomi, flussiPiano, evento, !!stato.attesa_ram]);
  if (firma === firmaSchermo) return;
  firmaSchermo = firma;

  const g = schermoCtx, w = schermoCanvas.width, h = schermoCanvas.height;
  g.fillStyle = '#071128';
  g.fillRect(0, 0, w, h);

  // cornice e barra alta nel colore del piano
  g.strokeStyle = p.colore || NEON.ciano;
  g.lineWidth = 4;
  g.strokeRect(4, 4, w - 8, h - 8);
  g.fillStyle = 'rgba(20, 34, 68, .85)';
  g.fillRect(6, 6, w - 12, 68);

  g.textBaseline = 'middle';
  g.textAlign = 'left';
  g.fillStyle = '#eef4ff';
  g.font = '800 32px system-ui, "Segoe UI", sans-serif';
  g.fillText(`${p.emoji} ${p.nome}`, 26, 40, 560);

  g.textAlign = 'right';
  g.font = '700 24px system-ui, "Segoe UI", sans-serif';
  const statoAlto = lavNomi.length || codaNomi.length
    ? `${lavNomi.length} al lavoro · ${codaNomi.length} in coda`
    : `Operativo · ${p.agenti.length} agenti`;
  g.fillStyle = lavNomi.length ? NEON.lavora : codaNomi.length ? NEON.coda : '#9aa6c4';
  g.fillText(statoAlto, w - 26, 40, 380);

  // divisore centrale
  g.strokeStyle = 'rgba(120, 200, 255, .25)';
  g.lineWidth = 2;
  g.beginPath();
  g.moveTo(512, 88);
  g.lineTo(512, h - 22);
  g.stroke();

  // colonna sinistra: lavorando, in coda e scambi attivi
  g.textAlign = 'left';
  g.fillStyle = '#7fe0ff';
  g.font = '700 19px system-ui, "Segoe UI", sans-serif';
  g.fillText('IN ATTIVITÀ SUL PIANO', 26, 102);

  const righeAttivi = [
    ...lavNomi.map((n) => ({ nome: n, stato: 'lavora', col: NEON.lavora })),
    ...codaNomi.map((n) => ({ nome: n, stato: 'in coda', col: NEON.coda })),
  ];
  let y = 142;
  if (!righeAttivi.length) {
    g.fillStyle = '#9aa6c4';
    g.font = '600 23px system-ui, "Segoe UI", sans-serif';
    g.fillText('Nessun agente impegnato al momento', 26, y, 465);
  } else {
    g.font = '700 23px system-ui, "Segoe UI", sans-serif';
    for (const r of righeAttivi.slice(0, 4)) {
      g.fillStyle = r.col;
      g.beginPath();
      g.arc(36, y, 7, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = '#eef4ff';
      g.fillText(`${r.nome} · ${r.stato}`, 54, y, 440);
      y += 38;
    }
    if (righeAttivi.length > 4) {
      g.fillStyle = '#9aa6c4';
      g.font = '600 19px system-ui, "Segoe UI", sans-serif';
      g.fillText(`+ altri ${righeAttivi.length - 4}`, 54, y);
    }
  }

  // scambi attivi in basso a sinistra
  g.fillStyle = '#7fe0ff';
  g.font = '700 18px system-ui, "Segoe UI", sans-serif';
  g.fillText('SCAMBI TRA PIANI', 26, 304);
  g.font = '600 20px system-ui, "Segoe UI", sans-serif';
  if (flussiPiano.length) {
    const f = flussiPiano[0];
    const altro = f.a_piano === p.id ? f.da_piano : f.a_piano;
    const verso = f.a_piano === p.id ? `← Da ${altro}` : `→ Verso ${altro}`;
    g.fillStyle = '#ffd27a';
    g.fillText(`${verso} (${f.tipo})`, 26, 338, 465);
  } else {
    g.fillStyle = '#9aa6c4';
    g.fillText(stato.attesa_ram ? '⚠ In attesa di RAM sul Pi' : 'Nessuna delega attiva', 26, 338, 465);
  }

  // colonna destra: ultimo evento e commessa attiva
  g.fillStyle = '#7fe0ff';
  g.font = '700 19px system-ui, "Segoe UI", sans-serif';
  g.fillText('ULTIMO EVENTO & COMMESSA', 538, 102);

  let yEv = 144;
  if (evento?.commessa) {
    g.fillStyle = 'rgba(79, 227, 255, .16)';
    g.fillRect(538, yEv - 18, 456, 38);
    g.fillStyle = NEON.ciano;
    g.font = '700 21px system-ui, "Segoe UI", sans-serif';
    g.fillText(`COMMESSA: ${evento.commessa}`, 552, yEv + 1, 430);
    yEv += 52;
  }

  g.fillStyle = evento?.testo ? '#eef4ff' : '#9aa6c4';
  g.font = '600 23px system-ui, "Segoe UI", sans-serif';
  const testoEv = evento?.testo || `Capo: ${capoDi(p)?.nome || '—'} · In attesa di attività`;
  for (const riga of righeTesto(g, testoEv, 456, 3)) {
    g.fillText(riga, 538, yEv);
    yEv += 34;
  }
  if (evento?.t) {
    g.fillStyle = '#9aa6c4';
    g.font = '600 19px system-ui, "Segoe UI", sans-serif';
    const oraStr = String(evento.t).slice(11, 16) || String(evento.t);
    g.fillText(`Aggiornato alle ${oraStr}`, 538, 338);
  }
  schermoTex.needsUpdate = true;
}
// lavagna olografica del capo
const LAVAGNA = disegnata(256, 176, (g, w, h) => {
  g.strokeStyle = NEON.ciano;
  g.shadowColor = NEON.ciano;
  g.shadowBlur = 10;
  g.lineWidth = 4;
  g.strokeRect(6, 6, w - 12, h - 12);
  g.fillStyle = 'rgba(79, 227, 255, .14)';
  g.fillRect(6, 6, w - 12, h - 12);
  g.fillStyle = NEON.ciano;
  g.fillRect(22, 22, 110, 8);
  for (let i = 0; i < 4; i++) {
    g.fillRect(22, 50 + i * 28, 14, 14);
    g.fillRect(46, 54 + i * 28, 70 + caso(i) * 40, 6);
  }
  for (let i = 0; i < 5; i++) g.fillRect(150 + i * 18, 150 - 20 - caso(i + 9) * 90, 11, 20 + caso(i + 9) * 90);
});
const MAT_LAVAGNA = new THREE.MeshBasicMaterial({ map: LAVAGNA, transparent: true, opacity: 0.85, side: THREE.DoubleSide,
  blending: THREE.AdditiveBlending, depthWrite: false });
// neon della stanza: pulsano piano nel loop
const NEON_PIANO = new THREE.MeshBasicMaterial(), NEON_VETRI = new THREE.MeshBasicMaterial({ color: NEON.ciano });

// dove si ferma chi va a trovare b: nel corridoio dietro di lui, un po' a sinistra (cosi' tutti e due guardano la camera);
// dai capi, dietro a sinistra dentro l'ufficio
function postoVisita(b) {
  return b.x < 0 ? [b.x - 0.5 * b.scala, b.z - 0.5 * b.scala - 0.5] : [b.x - 1.3, b.z - CORRIDOIO];
}

function costruisciPiano(p) {
  const gruppo = new THREE.Group(), propria = (m) => { m.userData.propria = true; return m; };
  const capi = p.agenti.filter((a) => a.x < 0), griglia = p.agenti.filter((a) => a.x >= 0);
  const gx = griglia.length ? Math.max(...griglia.map((a) => a.x)) : 0;
  const gz = griglia.length ? Math.max(...griglia.map((a) => a.z)) : 0;
  const corsie = [CORSIA_SINISTRA, gx + 2.0];  // le due corsie lungo z ai lati delle file
  const ux0 = capi.length ? Math.min(...capi.map((a) => a.x - 1.05 * a.scala)) - 0.4 : 0;
  const uz0 = capi.length ? Math.min(...capi.map((a) => a.z - 0.5 * a.scala - 1.3)) : 0;
  const uz1 = capi.length ? Math.max(...capi.map((a) => a.z + 1.4 * a.scala + 0.5)) : 0;
  const x0 = Math.min(ux0 - 0.6, -4), z0 = Math.min(-8, uz0 - 0.6), x1 = Math.max(gx + 4.5, 30), z1 = Math.max(gz + 5, uz1 + 1, 4);
  const parete = materiale('#283046', { roughness: 0.6 });
  NEON_PIANO.color.set(p.colore);
  NEON_PIANO.userData.base = NEON_PIANO.color.clone();

  // pavimento scuro lucido, pareti col filo al neon del reparto
  gruppo.add(
    scatola(x0, x1, -0.4, 0, z0, z1, materiale('#2a3040', { roughness: 0.2, metalness: 0.25 })),
    scatola(x0, x1, 0, ALTEZZA_PARETE, z0, z0 + 0.3, parete),
    scatola(x0, x0 + 0.3, 0, ALTEZZA_PARETE, z0, z1, parete),
    scatola(x0 + 0.3, x1, 0, 0.12, z0 + 0.3, z0 + 0.36, NEON_PIANO),
    scatola(x0 + 0.3, x0 + 0.36, 0, 0.12, z0 + 0.3, z1, NEON_PIANO),
    scatola(x0 + 0.3, x1, ALTEZZA_PARETE - 0.1, ALTEZZA_PARETE, z0 + 0.3, z0 + 0.36, NEON_PIANO),
  );
  // vetrate sulla citta' di notte a sinistra
  const lungo = z1 - z0 - 1.2, vetrata = new THREE.PlaneGeometry(lungo, 2.6).rotateY(Math.PI / 2);
  const uv = vetrata.attributes.uv;
  for (let i = 0; i < uv.count; i++) uv.setX(i, uv.getX(i) * lungo / 8);
  const vetri = propria(new THREE.Mesh(vetrata, MAT_VETRATE));
  vetri.position.set(x0 + 0.31, 2.1, (z0 + z1) / 2);
  // maxischermo sulla parete di fondo
  preparaSchermo();
  firmaSchermo = '';
  aggiornaSchermo(p);
  const largo = Math.min(18, x1 - 8), tvx = (2 + x1) / 2;
  const tv = propria(new THREE.Mesh(new THREE.PlaneGeometry(largo, largo * 0.375), schermo));
  tv.position.set(tvx, 2.35, z0 + 0.32);
  gruppo.add(vetri, tv, scatola(tvx - largo / 2 - 0.15, tvx + largo / 2 + 0.15,
    2.35 - largo * 0.19 - 0.15, 2.35 + largo * 0.19 + 0.15, z0 + 0.28, z0 + 0.31, materiale('#0a0d16')));

  // angolo lounge (divani grigi e tavolino) e tavolo riunioni in legno, nella fascia fra lo schermo e la prima fila
  const fondo = z0 + 0.5, davanti = -2.5, divano = materiale('#5d6476', { roughness: 0.9 }), legno = materiale('#8d6a48', { roughness: 0.55 });
  if (davanti - fondo > 3) {
    const lx = 3;
    gruppo.add(
      scatola(lx, lx + 3.2, 0, 0.5, fondo + 0.4, fondo + 1.4, divano), scatola(lx, lx + 3.2, 0.5, 1.2, fondo + 0.4, fondo + 0.7, divano),
      scatola(lx - 1.1, lx, 0, 0.5, fondo + 0.4, fondo + 3.2, divano), scatola(lx - 1.1, lx - 0.8, 0.5, 1.2, fondo + 0.4, fondo + 3.2, divano),
      scatola(lx + 0.6, lx + 2.4, 0, 0.42, fondo + 1.9, fondo + 2.9, legno),
      scatola(lx - 1.4, lx + 3.6, 0, 0.03, fondo + 0.1, fondo + 3.5, materiale('#2a3046')),
    );
    const tx = x1 - 9, tz = (fondo + davanti) / 2;
    gruppo.add(scatola(tx - 2.4, tx + 2.4, 0.72, 0.82, tz - 0.9, tz + 0.9, legno), scatola(tx - 0.3, tx + 0.3, 0, 0.72, tz - 0.3, tz + 0.3, legno));
    for (let k = 0; k < 3; k++) {
      for (const lato of [-1, 1]) gruppo.add(mesh(GEO.sedia, MAT.sedia, tx - 1.6 + k * 1.6, 0, tz + lato * 1.35, 1.1));
    }
  }

  // isole dei team: tappeto scuro nel tono del reparto, pareti di vetro fra un'isola e l'altra (aperte sulle corsie)
  // isole = file consecutive: la pianta lascia una fila vuota fra un gruppo e l'altro
  const isole = [];
  for (const z of [...new Set(griglia.map((a) => a.z))].sort((a, b) => a - b)) {
    const ultima = isole.at(-1);
    if (ultima && z - ultima.z1 < 3 * ALLARGA.z) ultima.z1 = z;
    else isole.push({ z0: z, z1: z });
  }
  for (const b of isole) {
    const xs = griglia.filter((a) => a.z >= b.z0 && a.z <= b.z1).map((a) => a.x);
    Object.assign(b, { x0: Math.min(...xs), x1: Math.max(...xs) });
  }
  const ordinate = isole, hsl = {};
  new THREE.Color(p.colore).getHSL(hsl);
  const piante = [[x1 - 0.9, z0 + 0.9], [x1 - 0.9, z1 - 0.9], [x0 + 0.9, z1 - 0.9]];
  ordinate.forEach((b, i) => {
    const tappeto = new THREE.Color().setHSL((hsl.h + i * 0.05) % 1, 0.35, 0.13 + (i % 2) * 0.03);
    gruppo.add(scatola(b.x0 - 1.6, b.x1 + 1.3, 0, 0.03, b.z0 - 0.6, b.z1 + 4.7, materiale(`#${tappeto.getHexString()}`, { roughness: 0.9 })));
    // il nome del team scritto a terra dopo l'ultima fila dell'isola, verso la camera: dietro la prima fila lo
    // coprirebbero i robot (il gruppo senza team prende il nome del reparto)
    const conta = new Map();
    for (const a of griglia) if (a.z >= b.z0 && a.z <= b.z1 && a.team) conta.set(a.team, (conta.get(a.team) || 0) + 1);
    const nome = [...conta].sort((x, y) => y[1] - x[1])[0]?.[0] || p.nome, largo = Math.min(b.x1 - b.x0 + 2.6, 16);
    const scritto = new THREE.MeshBasicMaterial({ map: scritta(nome, p.colore, largo, 3, false, true), transparent: true, depthWrite: false });
    scritto.userData.propria = true;  // texture fatta per questo piano: si butta alla chiusura
    const testo = aTerra(scritto, b.x0 - 1.5 + largo / 2, 0.05, b.z1 + 3.2, largo, 3);  // passa sotto il vetro
    testo.geometry = new THREE.PlaneGeometry(1, 1);
    gruppo.add(propria(testo));
    piante.push([corsie[1] + 1.5, (b.z0 + b.z1) / 2 + 0.5]);
    const dopo = ordinate[i + 1];
    if (!dopo) return;
    const z = (b.z1 + dopo.z0) / 2 + 0.4, xa = corsie[0] + 0.8, xb = corsie[1] - 0.8;
    gruppo.add(scatola(xa, xb, 0, 1.3, z - 0.04, z + 0.04, materiale('#9fdcff', VETRO)),
      scatola(xa, xb, 1.3, 1.36, z - 0.05, z + 0.05, NEON_VETRI),
      scatola(xa - 0.06, xa + 0.06, 0, 1.36, z - 0.06, z + 0.06, NEON_VETRI),
      scatola(xb - 0.06, xb + 0.06, 0, 1.36, z - 0.06, z + 0.06, NEON_VETRI));
  });
  const vasi = instanziato(GEO_VASO, MAT_VASO, piante.length), foglie = instanziato(GEO_FOGLIE, MAT_FOGLIE, piante.length);
  const m = new THREE.Matrix4();
  piante.forEach(([x, z], i) => {
    vasi.setMatrixAt(i, m.makeScale(1.4, 1.4, 1.4).setPosition(x, 0, z));
    foglie.setMatrixAt(i, m.makeRotationY(i * 1.3).scale(new THREE.Vector3(1.4, 1.6, 1.4)).setPosition(x, 0, z));
  });
  gruppo.add(vasi, foglie);

  // ufficio vetrato del capo (e del Direttore): porte sulla corsia, alla quota dove si fermano i visitatori
  if (capi.length) {
    const ux1 = PARETE_UFFICIO, vetro = materiale('#9cc7e8', VETRO), filo = NEON_VETRI;
    gruppo.add(scatola(ux0, ux1, 0, 0.03, uz0, uz1, materiale(mescola(p.colore, NOTTE, 0.6))));
    const porte = capi.map((a) => postoVisita(a)[1]).sort((a, b) => a - b);
    const tratti = [[ux0, ux1, uz0, uz0 + 0.08], [ux0, ux1, uz1 - 0.08, uz1], [ux0, ux0 + 0.08, uz0, uz1]];
    let da = uz0;
    for (const zp of porte) {
      if (zp - 0.8 > da) tratti.push([ux1 - 0.08, ux1, da, zp - 0.8]);
      da = Math.max(da, zp + 0.8);
    }
    if (uz1 > da) tratti.push([ux1 - 0.08, ux1, da, uz1]);
    for (const [a0, a1, b0, b1] of tratti) gruppo.add(scatola(a0, a1, 0, 2.6, b0, b1, vetro), scatola(a0, a1, 2.6, 2.66, b0, b1, filo));
    // lavagna olografica accanto al capo
    const capo = capoDi(p) ?? capi[0];
    const lavagna = propria(new THREE.Mesh(new THREE.PlaneGeometry(1.9, 1.3), MAT_LAVAGNA));
    lavagna.position.set(Math.min(capo.x + 0.9 * capo.scala, ux1 - 0.9), 2.5, capo.z + 0.2);
    lavagna.rotation.y = -0.35;
    gruppo.add(lavagna);
  }

  const robot = robottini(p), attivi = new THREE.Group(), chiacchiere = new THREE.Group();
  gruppo.add(robot.gruppo, attivi, chiacchiere);
  const box = new THREE.Box3(new THREE.Vector3(x0, 0, z0), new THREE.Vector3(x1, 2.6, z1));
  // il raycast sulle istanze passa prima da una sfera calcolata una volta sola dalle posizioni sedute:
  // la si fissa a tutto il piano, cosi' anche chi cammina negli angoli si puo' cliccare
  const sfera = box.getBoundingSphere(new THREE.Sphere());
  sfera.radius += 3;
  for (const parte of ['corpo', 'testa', 'visiera']) robot[parte].boundingSphere = sfera.clone();
  return { piano: p, gruppo, robot, attivi, chiacchiere, box, corsie, tipi: p.agenti.map(() => 'fermo'), icone: [],
    cam: new Map(), girati: new Map(), pop: new Map(), prossimo: performance.now() + 1200 };
}
const GEO_VASO = new THREE.CylinderGeometry(0.32, 0.24, 0.5, 10).translate(0, 0.25, 0);
const GEO_FOGLIE = new THREE.IcosahedronGeometry(0.5, 0).scale(1, 1.3, 1).translate(0, 1.05, 0);
const MAT_VASO = new THREE.MeshStandardMaterial({ color: '#e7dccb', roughness: 0.7 });
const MAT_FOGLIE = new THREE.MeshStandardMaterial({ color: '#3fae6a', roughness: 0.6, flatShading: true });

// ===== Uffici vivi: il regista manda i robot liberi a parlare con un collega del piano =====
// nuvoletta olografica: due materiali, cosi' i due a turno "parlano"
function nuvoletta() {
  const tex = disegnata(128, 112, (g) => {
    g.shadowColor = NEON.ciano;
    g.shadowBlur = 12;
    g.fillStyle = 'rgba(8, 18, 40, .78)';
    g.strokeStyle = NEON.ciano;
    g.lineWidth = 6;
    g.beginPath();
    g.roundRect(12, 12, 104, 64, 22);
    g.moveTo(44, 76); g.lineTo(36, 100); g.lineTo(64, 76);
    g.fill();
    g.stroke();
    g.fillStyle = '#e8fbff';
    for (const x of [42, 64, 86]) { g.beginPath(); g.arc(x, 44, 7, 0, Math.PI * 2); g.fill(); }
  });
  return new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true });
}
const BOLLE = [nuvoletta(), nuvoletta()];

// percorso a gomito sui corridoi: in piedi dietro la sedia, lungo la fila fino a una corsia (se serve), lungo la corsia
// fino alla fila del collega, lungo quella fino a lui. Le linee di corridoio stanno fra lo schienale e la scrivania
// della fila dietro, le corsie ai due lati delle file: nessun tratto passa sopra una scrivania.
function percorso(pa, a, b) {
  const [vx, vz] = postoVisita(b), zs = a.z - CORRIDOIO;
  const punti = [[a.x, a.z], [a.x, zs]];
  if (b.x >= 0 && Math.abs(zs - vz) < 0.01) return [...punti, [vx, vz]];
  const costo = (k) => Math.abs(a.x - k) + Math.abs(vx - k);
  const corsia = b.x < 0 ? pa.corsie[0] : pa.corsie.reduce((m, k) => (costo(k) < costo(m) ? k : m));
  return [...punti, [corsia, zs], [corsia, vz], [vx, vz]];
}

function regista(pa) {
  const { piano: p, tipi, cam } = pa;
  if (cam.size >= (telefono() ? 3 : 6)) return;
  const occupati = new Set([...pa.girati.keys(), ...pa.pop.keys()]);
  for (const w of cam.values()) occupati.add(w.i).add(w.j);
  const liberi = p.agenti.flatMap((a, i) => (tipi[i] === 'fermo' && !occupati.has(i) ? [i] : []));
  const camminatori = liberi.filter((i) => p.agenti[i].x >= 0);
  if (!camminatori.length) return;
  const i = camminatori[Math.floor(Math.random() * camminatori.length)], a = p.agenti[i];
  // vicini = a pochi passi lungo i corridoi (chi sta nella stessa fila e' piu' vicino di chi sta in quella davanti)
  const strada = (j) => percorso(pa, a, p.agenti[j]).reduce((t, [x, z], k, pt) => (k ? t + Math.abs(x - pt[k - 1][0]) + Math.abs(z - pt[k - 1][1]) : 0), 0);
  const vicini = (lista) => lista.filter((j) => j !== i).map((j) => [j, strada(j)]).sort((x, y) => x[1] - y[1]).map(([j]) => j);
  // di preferenza un collega vicino dello stesso team; ogni tanto il capo o il comanager
  const capi = vicini(liberi.filter((j) => ['direttore', 'capo', 'comanager'].includes(p.agenti[j].ruolo))).slice(0, 2);
  const team = vicini(liberi.filter((j) => a.team && p.agenti[j].team === a.team)).slice(0, 6);
  let scelta = Math.random() < 0.2 && capi.length ? capi : team;
  if (!scelta.length) scelta = capi.length ? capi : vicini(liberi).slice(0, 6);
  if (!scelta.length) return;
  const j = scelta[Math.floor(Math.random() * scelta.length)];
  cam.set(i, { i, j, punti: percorso(pa, a, p.agenti[j]), seg: 0, d: 0, fase: 'andata', x: a.x, z: a.z,
    angolo: 0, verso: Math.PI, fine: 0, bolle: [] });
}

const angoloVerso = (da, a, k) => da + (Math.atan2(Math.sin(a - da), Math.cos(a - da))) * k;

// un passo del camminatore w
function cammina(pa, w, ora, dt, t) {
  const p = pa.piano, a = p.agenti[w.i], b = p.agenti[w.j];
  if (w.fase === 'parla') {
    if (ora >= w.fine) torna(pa, w);
  } else {
    let resto = VELOCITA * dt;
    while (resto > 0 && w.seg < w.punti.length - 1) {
      const [ax, az] = w.punti[w.seg], [bx, bz] = w.punti[w.seg + 1], lungo = Math.hypot(bx - ax, bz - az);
      const passo = Math.min(resto, lungo - w.d);
      if (lungo > 0) w.verso = Math.atan2(bx - ax, bz - az);
      w.d += passo;
      resto -= passo;
      if (w.d >= lungo - 1e-6) { w.seg++; w.d = 0; }
    }
    if (w.seg < w.punti.length - 1) {
      const [ax, az] = w.punti[w.seg], [bx, bz] = w.punti[w.seg + 1], k = w.d / (Math.hypot(bx - ax, bz - az) || 1);
      w.x = ax + (bx - ax) * k;
      w.z = az + (bz - az) * k;
    } else {
      [w.x, w.z] = w.punti.at(-1);
      if (w.fase === 'ritorno') {  // di nuovo seduto
        pa.cam.delete(w.i);
        siedi(pa, w.i);
        return;
      }
      // arrivato: si parla per 3-6 s, il collega si gira un po' verso di lui
      w.fase = 'parla';
      w.fine = ora + 3000 + Math.random() * 3000;
      // le nuvolette si aprono verso l'esterno della coppia, cosi' non si coprono
      w.bolle = [[BOLLE[0], w.x, a.scala, w.z, 0.85], [BOLLE[1], b.x, b.scala, b.z, 0.15]].map(([mat, x, s, z, cx]) => {
        const bolla = new THREE.Sprite(mat);
        bolla.center.set(cx, 0);
        bolla.position.set(x, 2.15 * s, z);
        pa.chiacchiere.add(bolla);
        return bolla;
      });
      pa.girati.set(w.j, { angolo: pa.girati.get(w.j)?.angolo ?? 0, verso: -0.6 });
    }
  }
  const voluto = w.fase === 'parla' ? Math.atan2(b.x - w.x, b.z - w.z) : w.verso;
  w.angolo = angoloVerso(w.angolo, voluto, 1 - Math.exp(-10 * dt));
  const passi = w.fase !== 'parla', ritmo = Math.sin(t * 9 + w.i);
  posa(pa.robot, w.i, w.x, passi ? Math.abs(ritmo) * 0.1 : 0, w.z, w.angolo, a.scala, passi
    ? { rollio: ritmo * 0.07, bracci: [ritmo * 0.5, -ritmo * 0.5] }
    : { cenno: Math.sin(t * 6) * 0.05, bracci: [-0.5 + Math.sin(t * 4 + w.i) * 0.35, -0.15] });
}

// fine della chiacchierata (o il collega si e' messo a lavorare): si torna per la stessa strada
function torna(pa, w) {
  if (w.fase === 'ritorno') return;
  for (const b of w.bolle) pa.chiacchiere.remove(b);
  w.bolle = [];
  const g = pa.girati.get(w.j);
  if (g) g.verso = 0;
  w.punti = [[w.x, w.z], ...w.punti.slice(0, w.seg + 1).reverse()];
  Object.assign(w, { fase: 'ritorno', seg: 0, d: 0 });
}

// riporta subito al posto il robot i (si e' messo a lavorare, o lo si cerca): ricompare con un piccolo "pop"
function richiama(pa, i) {
  const w = pa.cam.get(i);
  if (!w) return;
  torna(pa, w);
  pa.cam.delete(i);
  siedi(pa, i);
  if (vivo()) pa.pop.set(i, performance.now());
}

function siedi(pa, i) {
  const a = pa.piano.agenti[i];
  posa(pa.robot, i, a.x, 0, a.z, 0, a.scala);
}

// un frame della vista piano: regista, camminate, chi scrive, chi si gira, icone che fluttuano, neon, schermi
function animaPiano(ora, dt) {
  const pa = pianoAperto, p = pa.piano, t = ora / 1000;
  if (ora >= pa.prossimo) {
    regista(pa);
    pa.prossimo = ora + 3000 + Math.random() * 4000;
  }
  for (const w of [...pa.cam.values()]) cammina(pa, w, ora, dt, t);
  const seduti = new Set([...pa.girati.keys(), ...pa.pop.keys()]);
  pa.tipi.forEach((tipo, i) => { if (tipo !== 'fermo') seduti.add(i); });
  for (const i of seduti) {
    if (pa.cam.has(i)) continue;
    const a = p.agenti[i], g = pa.girati.get(i), inizio = pa.pop.get(i);
    let s = a.scala, angolo = 0;
    if (g) {
      g.angolo = angoloVerso(g.angolo, g.verso, 1 - Math.exp(-6 * dt));
      angolo = g.angolo;
      if (!g.verso && Math.abs(g.angolo) < 0.01) { pa.girati.delete(i); angolo = 0; }
    }
    if (inizio !== undefined) {
      const k = Math.min(1, (ora - inizio) / 300);
      s *= 0.6 + 0.4 * (1 - (1 - k) ** 3);
      if (k === 1) pa.pop.delete(i);
    }
    const scrive = pa.tipi[i] !== 'fermo';
    posa(pa.robot, i, a.x, 0, a.z, angolo, s, scrive ? {
      cenno: 0.06 + Math.sin(t * 8 + i) * 0.04,
      bracci: [SEDUTO[0] + Math.sin(t * 16 + i) * 0.13, SEDUTO[1] + Math.sin(t * 16 + i + Math.PI) * 0.13] } : undefined);
  }
  // icone che fluttuano, nuvolette che si alternano, neon che pulsano, codice che scorre e sfarfalla
  for (const o of pa.icone) o.position.y = o.userData.y + Math.sin(t * 1.8 + o.userData.fase) * 0.15;
  OLOGRAMMA.lavora.rotation = OLOGRAMMA.coda.rotation = Math.sin(t * 0.9) * 0.12;
  const turno = Math.floor(t / 1.3) % 2;
  BOLLE[0].opacity = turno ? 0.35 : 1;
  BOLLE[1].opacity = turno ? 1 : 0.35;
  NEON_PIANO.color.copy(NEON_PIANO.userData.base).multiplyScalar(0.75 + 0.25 * Math.sin(t * 1.4));
  NEON_VETRI.color.set(NEON.ciano).multiplyScalar(0.7 + 0.3 * Math.sin(t * 1.1 + 2));
  for (const m of Object.values(ARCO)) m.opacity = 0.6 + 0.3 * Math.sin(t * 3);
  MAT_LAVAGNA.opacity = 0.75 + 0.15 * Math.sin(t * 2.3);
  CODICE.offset.y = (t * 0.04) % 1;
  MAT.schermo.color.setScalar(0.93 + 0.07 * Math.sin(t * 37) * Math.sin(t * 11));
}

// ===== Colori dallo stato =====
function applicaStato() {
  const ins = insiemi();
  if (pianoAperto) coloraPiano(ins);
  for (const c of cassetti.values()) statoFuori(c, ins);
  aggiornaFlussiEsterni();
  aggiornaHud();
  aggiornaElenco();
  richiediRender();
}

function coloraPiano(ins) {
  const { lav, coda } = ins;
  const pa = pianoAperto, prima = pa.tipi;
  pa.tipi = luciRobot(pa.robot, pa.piano, lav, coda);
  // chi ha smesso di lavorare torna composto (niente testa o braccia ferme a meta' battuta)
  pa.tipi.forEach((tipo, i) => {
    if (tipo === 'fermo' && prima[i] !== 'fermo' && !pa.cam.has(i) && !pa.girati.has(i) && !pa.pop.has(i)) siedi(pa, i);
  });
  // chi e' in giro e ora lavora torna subito al posto; se si mette a lavorare il collega, la visita finisce
  for (const w of [...pa.cam.values()]) {
    if (pa.tipi[w.i] !== 'fermo') richiama(pa, w.i);
    else if (pa.tipi[w.j] !== 'fermo') torna(pa, w);
  }
  for (const [j, g] of pa.girati) if (pa.tipi[j] !== 'fermo') g.verso = 0;
  pa.icone = segniStato(pa.attivi, pa.piano, pa.tipi, true);
  aggiornaSchermo(pa.piano, ins);
}

// ===== Navigazione =====
function chiudiPiano() {
  if (!pianoAperto) return;
  scena.remove(pianoAperto.gruppo);
  pianoAperto.gruppo.traverse((o) => {
    if (o.isInstancedMesh) o.dispose();
    if (o.userData.propria) o.geometry.dispose();
    if (o.material?.userData?.propria) {
      o.material.map.dispose();
      o.material.dispose();
    }
  });
  pianoAperto = null;
}

function apriPiano(idPiano) {
  const p = edificio?.piani.find((x) => x.id === idPiano);
  if (!p) return;
  chiudiPiano();
  chiudiCassetti();
  pianoAperto = costruisciPiano(p);
  scena.add(pianoAperto.gruppo);
  esterno.visible = false;
  luciScena(true);
  hud.hidden = true;
  $('legenda').hidden = false;
  $('suggerimento').hidden = true;
  $('titolo').textContent = `${p.emoji} ${p.nome}`;
  $('sottotitolo').textContent = `Piano ${p.numero}, capo ${capoDi(p)?.nome || '—'}, ${p.agenti.length} agenti`;
  $('indietro').hidden = false;
  inquadra(pianoAperto.box);
  applicaStato();
}

function tornaFuori() {
  chiudiPiano();
  esterno.visible = true;
  luciScena(false);
  richiediRender();
  hud.hidden = false;
  $('legenda').hidden = true;
  $('suggerimento').hidden = true;
  $('titolo').textContent = 'Ufficio';
  $('indietro').hidden = true;
  firmaHud = '';
  aggiornaHud();
  // l'HUD sta a destra sul computer, in basso sul telefono
  if (innerWidth < 700) inquadra(boxEsterno, 0, 0.6);
  else inquadra(boxEsterno, 0.5);
}

// apre il piano dell'agente, lo richiama al posto se era in giro, centra la camera su di lui e apre il suo pannello
function volaVerso(idAgente) {
  const a = agentiPerId.get(idAgente);
  if (!a) return;
  if (pianoAperto?.piano.id !== a.piano) apriPiano(a.piano);
  else richiama(pianoAperto, pianoAperto.piano.agenti.indexOf(a));
  centro.set(a.x, 1, a.z);
  camera.zoom = Math.max(1, camera.top / 7);
  // sul telefono il pannello copre la parte bassa: l'agente va nel terzo alto dello schermo
  if (innerWidth < 700) centro.addScaledVector(SU, -0.6 * camera.top / camera.zoom);
  aggiornaCamera();
  apriAgente(idAgente);
}

// ===== Pannello =====
// Tutti i testi entrano con textContent, mai con innerHTML: le risposte degli agenti possono contenere HTML.
let aperture = 0;        // cresce a ogni apertura e chiusura: le risposte arrivate tardi per un pannello vecchio si buttano
let timerAgente = 0;     // rilettura dell'agente aperto; uno solo alla volta
let daRidareFuoco = null;
// ultimo messaggio mandato a ogni agente, {testo, t} con t = ora del server: l'Ufficio lo scrive in cronologia
// solo quando arriva la risposta (anche minuti dopo, in coda per Claude), intanto la pagina lo mostra "in attesa"
const inviati = new Map();

// il Direttore risponde con una riga JSON ({"risposta": …} o {"agente": …, "brief": …}): si mostra in chiaro
function testoAgente(testo) {
  let o;
  try { o = JSON.parse(testo); } catch { return testo; }
  if (o === null || typeof o !== 'object' || Array.isArray(o)) return testo;
  if (typeof o.risposta === 'string' && o.risposta) return o.risposta;
  if (typeof o.agente === 'string' && o.agente) {
    const nome = agentiPerId.get(o.agente)?.nome || o.agente;
    return typeof o.brief === 'string' && o.brief ? `Passo a ${nome}: ${o.brief}` : `Passo a ${nome}`;
  }
  return testo;
}

function el(tag, classe = '', testo = '') {
  const e = document.createElement(tag);
  if (classe) e.className = classe;
  if (testo) e.textContent = testo;
  return e;
}
function bottone(testo, classe, alClic) {
  const b = el('button', classe, testo);
  b.type = 'button';
  b.addEventListener('click', alClic);
  return b;
}
function nomePiano(id) {
  const p = edificio?.piani.find((x) => x.id === id);
  return p ? `${p.emoji} ${p.nome}` : id || '—';
}

function fermaMediaPannello() {
  for (const v of $('pannello-corpo').querySelectorAll('video')) {
    v.pause();
    v.muted = true;
  }
}

function apriPannello(titolo) {
  aperture++;
  clearInterval(timerAgente);
  fermaMediaPannello();
  const pannello = $('pannello');
  if (pannello.hidden) daRidareFuoco = document.activeElement;
  $('pannello-titolo').textContent = titolo;
  $('pannello-corpo').replaceChildren();
  pannello.hidden = false;
  pannello.focus({ preventScroll: true });
}
function chiudiPannello() {
  if ($('pannello').hidden) return;
  aperture++;
  clearInterval(timerAgente);
  fermaMediaPannello();
  $('pannello').hidden = true;
  if (daRidareFuoco?.isConnected) daRidareFuoco.focus({ preventScroll: true });
  daRidareFuoco = null;
}

function schedaAvatarGreta() {
  const box = el('section', 'avatar-greta');
  box.setAttribute('aria-label', 'Avatar 3D di Greta');
  const cornice = el('div', 'avatar-cornice');
  cornice.title = 'Clicca per cambiare dimensione';

  const video = el('video', 'avatar-media');
  video.src = 'assets/greta.mp4';
  video.poster = 'assets/greta-portrait.webp';
  video.loop = true;
  video.muted = true;
  video.playsInline = true;
  video.preload = 'metadata';

  const img = el('img', 'avatar-media');
  img.alt = 'Greta, segretaria personale in stile 3D';
  img.decoding = 'async';
  cornice.append(video, img);

  const controlli = el('div', 'avatar-controlli');
  const meta = el('div', 'avatar-meta');
  meta.append(el('strong', '', 'Greta · Piano 10'), el('span', '', 'Disegno 3D ufficiale'));

  const tabs = el('div', 'avatar-tabs');
  const azioni = el('div', 'avatar-azioni');
  const modi = [
    ['video', 'Video'],
    ['ritratto', 'Ritratto'],
    ['intera', 'Figura'],
  ];
  const bottoniModo = {};
  let modoCorrente = RIDOTTO ? 'ritratto' : 'video';

  const btnAudio = bottone('🔇 Audio', '', () => {
    if (modoCorrente !== 'video') setModo('video');
    video.muted = !video.muted;
    aggiornaAudio();
    if (video.paused) video.play().catch(() => {});
  });

  const btnEspandi = bottone('⤢ Grande', '', () => {
    const aperto = box.classList.toggle('espanso');
    btnEspandi.setAttribute('aria-pressed', String(aperto));
    btnEspandi.textContent = aperto ? '⤡ Riduci' : '⤢ Grande';
  });
  btnEspandi.setAttribute('aria-pressed', 'false');
  cornice.addEventListener('click', () => btnEspandi.click());

  function aggiornaAudio() {
    const attivo = !video.muted;
    btnAudio.textContent = attivo ? '🔊 Attivo' : '🔇 Audio';
    btnAudio.setAttribute('aria-pressed', String(attivo));
  }

  function setModo(modo) {
    modoCorrente = modo;
    cornice.dataset.modo = modo;
    for (const [chiave, b] of Object.entries(bottoniModo)) {
      b.setAttribute('aria-pressed', String(chiave === modo));
    }
    const usaVideo = modo === 'video';
    video.hidden = !usaVideo;
    img.hidden = usaVideo;
    if (usaVideo) {
      video.play().catch(() => {});
    } else {
      video.pause();
      if (!video.muted) {
        video.muted = true;
        aggiornaAudio();
      }
      img.src = modo === 'intera' ? 'assets/greta-full.webp' : 'assets/greta-portrait.webp';
    }
  }

  for (const [chiave, etichetta] of modi) {
    const b = bottone(etichetta, '', () => setModo(chiave));
    bottoniModo[chiave] = b;
    tabs.append(b);
  }
  azioni.append(btnAudio, btnEspandi);
  controlli.append(meta, tabs, azioni);
  box.append(cornice, controlli);

  aggiornaAudio();
  setModo(modoCorrente);
  return box;
}

function apriAgente(id) {
  const a = agentiPerId.get(id);
  if (!a) return;
  apriPannello(a.nome);
  const mia = aperture, corpo = $('pannello-corpo');
  const attesa = el('p', 'tenue', 'Carico…');
  corpo.append(attesa);
  let mostra = null, inCorso = false;
  const aggiorna = async () => {
    if (inCorso) return;
    inCorso = true;
    try {
      const d = await leggiJSON(`/api/agente/${encodeURIComponent(id)}`);
      if (mia !== aperture) return;
      mostra ??= schedaAgente(d, corpo, aggiorna);
      mostra(d);
    } catch {
      if (mia === aperture && !mostra) attesa.textContent = 'Non riesco a leggere questo agente.';
    } finally {
      inCorso = false;
    }
  };
  aggiorna();
  timerAgente = setInterval(aggiorna, INTERVALLO_STATO);
}

// costruisce la scheda una volta sola; restituisce la funzione che a ogni rilettura aggiorna chat e avviso
function schedaAgente(d, corpo, aggiorna) {
  const info = el('dl', 'info');
  const riga = (chiave, valore) => {
    const dd = el('dd');
    dd.append(valore);
    info.append(el('dt', '', chiave), dd);
  };
  riga('Ruolo', d.ruolo || '—');
  riga('Piano', nomePiano(d.piano));
  if (d.team) riga('Team', d.team);
  if (d.capo) {
    const capo = agentiPerId.get(d.capo);
    riga('Capo', capo ? bottone(capo.nome, 'collegamento', () => volaVerso(d.capo)) : d.capo);
  }

  // motore: se il server dice di no si torna al valore di prima
  const campoMotore = el('div', 'campo');
  const motore = el('select');
  motore.id = 'motore';
  for (const [valore, nome] of [['claude', 'Claude'], ['gemini', 'Gemini']]) {
    const o = el('option', '', nome);
    o.value = valore;
    motore.append(o);
  }
  motore.value = d.motore;
  let motorePrima = motore.value;
  const etichettaMotore = el('label', '', 'Motore');
  etichettaMotore.htmlFor = 'motore';
  const erroreMotore = el('p', 'errore');
  erroreMotore.setAttribute('role', 'alert');
  motore.addEventListener('change', async () => {
    motore.disabled = true;
    erroreMotore.textContent = '';
    try {
      await inviaJSON('/api/motore', { agente: d.id, motore: motore.value });
      motorePrima = motore.value;
    } catch (e) {
      motore.value = motorePrima;
      erroreMotore.textContent = e.message;
    } finally {
      motore.disabled = false;
    }
  });
  campoMotore.append(etichettaMotore, motore);
  const blocchi = d.id === 'segretaria' ? [schedaAvatarGreta()] : [];
  blocchi.push(info, el('p', 'descrizione', d.descrizione || ''), campoMotore, erroreMotore);
  corpo.replaceChildren(...blocchi);

  if (d.comando) corpo.append(copiaComando(d.comando));
  corpo.append(creaOrdine(d));

  // chat
  const lista = el('div', 'chat');
  lista.setAttribute('role', 'log');
  lista.setAttribute('aria-label', 'Chat');
  const avviso = el('p', 'avviso-chat');
  avviso.setAttribute('role', 'status');
  avviso.hidden = true;
  const modulo = el('form', 'invio');
  const testo = el('textarea');
  Object.assign(testo, { rows: 2, maxLength: 4000, placeholder: `Scrivi a ${d.nome}…` });
  testo.setAttribute('aria-label', `Messaggio per ${d.nome}`);
  const invia = el('button', 'primario', 'Invia');
  invia.type = 'submit';
  const errore = el('p', 'errore');
  errore.setAttribute('role', 'alert');
  modulo.append(testo, invia);
  corpo.append(lista, avviso, errore, modulo);

  let scorriGiu = false;
  modulo.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (invia.disabled || !testo.value.trim()) return;
    invia.disabled = true;
    errore.textContent = '';
    const messaggio = testo.value;
    try {
      const r = await inviaJSON('/api/chiedi', { agente: d.id, testo: messaggio });
      inviati.set(d.id, { testo: messaggio, t: typeof r.t === 'string' ? r.t : '' });
      testo.value = '';
      scorriGiu = true;
      if (ultimiDati) mostra(ultimiDati);  // il messaggio compare subito, senza aspettare la rilettura
      aggiorna();
      aggiornaStato();  // l'omino diventa verde senza aspettare il giro dei 3 s
    } catch (err) {
      errore.textContent = err.message;
    } finally {
      invia.disabled = false;
    }
  });
  testo.addEventListener('keydown', (e) => {  // Invio manda, Maiusc+Invio va a capo
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      modulo.requestSubmit();
    }
  });

  // orari tutti ISO "AAAA-MM-GGTHH:MM:SS" dell'orologio del server: si confrontano come stringhe
  let ultimaChat = '', ultimiDati = null;
  const mostra = (dati) => {
    ultimiDati = dati;
    const inviato = inviati.get(d.id);
    // in attesa finche' in cronologia non c'e' una tua riga scritta dopo l'invio
    const inAttesa = inviato && !dati.chat.some((m) => m.ruolo === 'utente' && (m.t || '') >= inviato.t);
    const firma = JSON.stringify([dati.chat, inAttesa ? inviato : null]);
    if (firma !== ultimaChat) {
      const inFondo = corpo.scrollHeight - corpo.scrollTop - corpo.clientHeight < 40;
      const primaVolta = !ultimaChat;
      ultimaChat = firma;
      const righe = dati.chat.map((m) => {
        const b = el('div', `msg ${m.ruolo}`, m.ruolo === 'agente' ? testoAgente(m.testo) : m.testo);
        b.title = m.t || '';
        return b;
      });
      if (inAttesa) {
        const b = el('div', 'msg utente in-attesa', inviato.testo);
        b.append(el('span', 'stato-msg', 'in attesa…'));
        righe.push(b);
      }
      lista.replaceChildren(...(righe.length ? righe : [el('p', 'tenue', 'Nessun messaggio. Scrivi qui sotto.')]));
      if (scorriGiu || (inFondo && !primaVolta)) corpo.scrollTop = corpo.scrollHeight;
      scorriGiu = false;
    }
    // gli avvisi (coda, errori, risposte passate dal Direttore) sono di tutta la pagina: si mostra l'ultimo
    // arrivato dopo il tuo ultimo invio, se in chat non c'e' gia' una riga piu' recente (la risposta)
    const ultimaRiga = dati.chat.at(-1)?.t || '';
    const a = inviato ? dati.avvisi.filter((x) => x.t >= inviato.t && x.t >= ultimaRiga).at(-1) : null;
    avviso.hidden = !a;
    avviso.textContent = a ? a.testo : '';
  };
  return mostra;
}

// su http:// via Tailscale navigator.clipboard non c'e': si mostra il comando gia' selezionato
function copiaComando(comando) {
  const zona = el('div', 'copia');
  const TESTO = 'Copia comando Claude Code';
  const b = bottone(TESTO, '', async () => {
    if (navigator.clipboard) {
      try {
        await navigator.clipboard.writeText(comando);
        b.textContent = 'Copiato ✓';
        setTimeout(() => { b.textContent = TESTO; }, 2000);
        return;
      } catch { /* negato: si passa al campo da copiare a mano */ }
    }
    const campo = el('input');
    Object.assign(campo, { readOnly: true, value: comando });
    campo.setAttribute('aria-label', 'Comando Claude Code');
    zona.replaceChildren(b, campo, el('p', 'tenue', 'Premi Ctrl+C (sul telefono: tieni premuto e copia)'));
    campo.focus();
    campo.select();
  });
  zona.append(b);
  return zona;
}

// Crea un ordine di lavoro in stato `piano`: l'approvazione avviene sempre da Telegram con /approva
function creaOrdine(d) {
  const zona = el('div', 'ordine-box');
  const esito = el('p', 'tenue');
  esito.hidden = true;
  const err = el('p', 'errore');
  err.setAttribute('role', 'alert');

  const apriModulo = () => {
    esito.hidden = true;
    err.textContent = '';
    const form = el('form', 'ordine-form');
    const titolo = el('input');
    Object.assign(titolo, { type: 'text', maxLength: 200, placeholder: 'Titolo breve (opzionale)' });
    titolo.setAttribute('aria-label', 'Titolo ordine di lavoro');
    const commessa = el('input');
    Object.assign(commessa, { type: 'text', maxLength: 200, placeholder: 'Commessa (es. Sito)' });
    commessa.setAttribute('aria-label', 'Commessa');
    const piano = el('textarea');
    Object.assign(piano, { rows: 3, maxLength: 4000, required: true, placeholder: `Descrivi il lavoro per ${d.nome}…` });
    piano.setAttribute('aria-label', 'Piano del lavoro');
    const salva = el('button', 'primario', 'Salva in piano');
    salva.type = 'submit';
    const annulla = bottone('Annulla', '', () => {
      zona.replaceChildren(apri, esito, err);
    });
    const azioni = el('div', 'campo');
    azioni.append(salva, annulla);
    form.append(titolo, commessa, piano, azioni);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (salva.disabled || !piano.value.trim()) return;
      salva.disabled = true;
      err.textContent = '';
      try {
        const r = await inviaJSON('/api/ordine', {
          agente: d.id,
          titolo: titolo.value.trim(),
          commessa: commessa.value.trim(),
          testo: piano.value.trim(),
        });
        esito.textContent = `Ordine ${r.id} creato in stato ${r.stato}. Approva su Telegram con /approva ${r.id}`;
        esito.hidden = false;
        zona.replaceChildren(apri, esito, err);
      } catch (ex) {
        err.textContent = ex.message;
      } finally {
        salva.disabled = false;
      }
    });
    zona.replaceChildren(form, err);
    piano.focus();
  };

  const apri = bottone('Crea ordine di lavoro', '', apriModulo);
  zona.append(apri, esito, err);
  return zona;
}

function apriStanza(id) {
  const s = edificio?.stanze.find((x) => x.id === id);
  if (!s) return;
  estrai(null);  // toccando una stanza col piano estratto, il piano rientra
  if (id === 'palestra' && s.link?.[0]?.url) {
    window.open(s.link[0].url, '_blank', 'noopener,noreferrer');
    return;
  }
  apriPannello(s.nome);
  if (!s.link.length) return $('pannello-corpo').append(el('p', 'tenue', 'Ancora vuota'));
  const ul = el('ul', 'link');
  for (const l of s.link) {
    // solo http, https e obsidian diventano link; il resto (javascript:, data:, …) resta testo
    let sicuro = false;
    try { sicuro = ['http:', 'https:', 'obsidian:'].includes(new URL(l.url).protocol); } catch { /* non e' un URL */ }
    const a = el(sicuro ? 'a' : 'span', '', l.testo);
    if (sicuro) Object.assign(a, { href: l.url, target: '_blank', rel: 'noopener noreferrer' });
    const li = el('li');
    li.append(a);
    ul.append(li);
  }
  $('pannello-corpo').append(ul);
}

$('chiudi-pannello').addEventListener('click', chiudiPannello);
$('indietro').addEventListener('click', tornaFuori);
$('btn-greta')?.addEventListener('click', () => {
  mostraElenco(false);
  volaVerso('segretaria');
});
addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  $('risultati').hidden = true;
  chiudiPannello();
  estrai(null);
});

// ===== Ricerca =====
const risultati = $('risultati');
let timerCerca = 0, ricerche = 0, cercando = Promise.resolve();

async function cerca() {
  const q = $('cerca').value.trim();
  const mia = ++ricerche;  // conta solo l'ultima risposta
  if (!q) return mostraRisultati([], '');
  let trovati;
  try {
    trovati = (await leggiJSON(`/api/cerca?q=${encodeURIComponent(q)}`)).risultati;
  } catch {
    if (mia === ricerche) mostraRisultati([], q, 'Ricerca non riuscita, riprova');
    return;
  }
  if (mia === ricerche) mostraRisultati(trovati, q);
}
function cercaOra() {
  clearTimeout(timerCerca);
  timerCerca = 0;
  cercando = cerca();
}

function mostraRisultati(trovati, q, errore = '') {
  risultati.replaceChildren(...trovati.map((r) => {
    const b = bottone(r.nome, '', () => scegli(r.id));
    b.append(el('span', 'tenue', ` · ${nomePiano(r.piano)}`));
    const li = el('li');
    li.append(b);
    return li;
  }));
  if (errore) risultati.append(el('li', 'errore vuoto', errore));
  else if (q && !trovati.length) risultati.append(el('li', 'tenue vuoto', 'Nessun agente trovato'));
  risultati.hidden = !risultati.children.length;
}

function scegli(id) {
  ricerche++;
  clearTimeout(timerCerca);
  $('cerca').value = '';
  mostraRisultati([], '');
  mostraElenco(false);
  volaVerso(id);
}

$('cerca').addEventListener('input', () => {
  clearTimeout(timerCerca);
  timerCerca = setTimeout(cercaOra, 300);
});
$('cerca').addEventListener('keydown', async (e) => {
  if (e.key === 'Enter') {
    // se c'e' una ricerca in attesa si fa subito e si sceglie il primo dei suoi risultati, non di quelli vecchi
    if (timerCerca) cercaOra();
    const mia = ricerche;
    await cercando;
    if (mia === ricerche) risultati.querySelector('button')?.click();
  } else if (e.key === 'ArrowDown') { e.preventDefault(); risultati.querySelector('button')?.focus(); }
});
risultati.addEventListener('keydown', (e) => {  // frecce fra i risultati
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
  e.preventDefault();
  const tutti = [...risultati.querySelectorAll('button')];
  const i = tutti.indexOf(document.activeElement) + (e.key === 'ArrowDown' ? 1 : -1);
  (i < 0 ? $('cerca') : tutti[Math.min(i, tutti.length - 1)]).focus();
});
document.addEventListener('pointerdown', (e) => {
  if (!e.target.closest('.ricerca')) risultati.hidden = true;
});

// ===== Elenco 2D =====
const statiElenco = new Map();  // id agente -> <span> con "· lavora" / "· in coda"

function costruisciElenco() {
  const lista = $('lista');
  const voce = (testo, alClic) => {
    const li = el('li');
    li.append(bottone(testo, '', alClic));
    return li;
  };
  for (const p of edificio.piani) {
    const det = el('details');
    const ul = el('ul');
    for (const a of p.agenti) {
      const li = voce(a.nome, () => apriAgente(a.id));
      const s = el('span', 'stato-agente');
      li.firstChild.append(s);
      statiElenco.set(a.id, s);
      ul.append(li);
    }
    det.append(el('summary', '', `${p.emoji} ${p.nome} (${p.agenti.length})`), ul);
    lista.append(det);
  }
  const stanze = el('ul', 'stanze');
  stanze.append(...edificio.stanze.map((s) => voce(s.nome, () => apriStanza(s.id))));
  lista.append(el('h2', '', 'Piano terra'), stanze);
  aggiornaElenco();
}

function aggiornaElenco() {
  const lav = new Set(stato.lavorando), coda = new Set(stato.in_coda);
  for (const [id, s] of statiElenco) {
    const tipo = lav.has(id) ? 'lavora' : coda.has(id) ? 'coda' : '';
    s.textContent = tipo === 'lavora' ? ' · lavora' : tipo === 'coda' ? ' · in coda' : '';
    s.className = `stato-agente ${tipo}`;
  }
}

function mostraElenco(si) {
  if (!edificio) return;
  if (si && !statiElenco.size) costruisciElenco();
  $('lista').hidden = !si;
  $('scena').hidden = si;  // dentro ci sono anche HUD e legenda
  $('elenco').textContent = si ? 'Edificio 3D' : 'Elenco';
  $('elenco').setAttribute('aria-pressed', String(si));
  if (!si) richiediRender();  // riparte il loop del piano, se ce n'e' uno aperto
}
$('elenco').addEventListener('click', () => mostraElenco($('lista').hidden));

// ===== Input: hover fa uscire un piano, clic entra; trascina, rotellina e due dita per la camera =====
const puntatori = new Map();  // pointerId -> { x, y }
let partenza = null, trascinato = false, distanzaDita = 0;
const distanzaFra = () => {
  const [a, b] = puntatori.values();
  return Math.hypot(a.x - b.x, a.y - b.y);
};

function zooma(fattore) {
  camera.zoom = THREE.MathUtils.clamp(camera.zoom * fattore, 0.4, 14);
  aggiornaCamera();
}

function sposta(dx, dy) {
  const unita = (camera.top - camera.bottom) / camera.zoom / (tela.clientHeight || 1);
  centro.addScaledVector(DESTRA, -dx * unita).addScaledVector(SU, dy * unita);
  aggiornaCamera();
}

const raggio = new THREE.Raycaster(), puntoSchermo = new THREE.Vector2();
function colpito(e) {
  if (!edificio) return null;
  const r = tela.getBoundingClientRect();
  puntoSchermo.set((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1);
  raggio.setFromCamera(puntoSchermo, camera);
  if (pianoAperto) {
    const { corpo, testa, visiera } = pianoAperto.robot;
    const hit = raggio.intersectObjects([corpo, testa, visiera], false)[0];
    return hit ? { agente: pianoAperto.piano.agenti[hit.instanceId] } : null;
  }
  return colpitoFuori();
}

function usaPuntatore(tipo) {
  if (tipo === ultimoPuntatore) return;
  ultimoPuntatore = tipo;
  aggiornaHud();
}

function clic(e) {
  const h = colpito(e);
  if (h?.agente) apriAgente(h.agente.id);
  else if (h?.stanza) apriStanza(h.stanza);
  else if (h?.piano) {
    // col mouse il piano e' gia' uscito all'hover; al tocco il primo tocco lo fa uscire, il secondo entra
    if (e.pointerType === 'mouse' || estratto === h.piano) apriPiano(h.piano);
    else estrai(h.piano);
  } else estrai(null);
}

function suggerisci(e) {
  if (e.pointerType !== 'mouse') return;
  usaPuntatore('mouse');
  const h = colpito(e);
  tela.style.cursor = h ? 'pointer' : '';
  estrai(h?.piano ?? null);
  const s = $('suggerimento');
  s.hidden = !h?.agente;
  if (h?.agente) {
    const r = contenitore.getBoundingClientRect();
    s.textContent = h.agente.nome;
    s.style.left = `${e.clientX - r.left}px`;
    s.style.top = `${e.clientY - r.top}px`;
  }
}

tela.addEventListener('pointerdown', (e) => {
  tela.setPointerCapture(e.pointerId);
  usaPuntatore(e.pointerType);
  $('suggerimento').hidden = true;
  puntatori.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (puntatori.size === 1) {
    partenza = { x: e.clientX, y: e.clientY };
    trascinato = false;
  } else {
    trascinato = true;
    distanzaDita = distanzaFra();
  }
});

tela.addEventListener('pointermove', (e) => {
  const prima = puntatori.get(e.pointerId);
  if (!prima) return suggerisci(e);
  const ora = { x: e.clientX, y: e.clientY };
  puntatori.set(e.pointerId, ora);
  if (puntatori.size >= 2) {
    const d = distanzaFra();
    if (distanzaDita > 0 && d > 0) zooma(d / distanzaDita);
    distanzaDita = d;
    return;
  }
  if (!trascinato && Math.hypot(ora.x - partenza.x, ora.y - partenza.y) > 6) trascinato = true;
  if (trascinato) sposta(ora.x - prima.x, ora.y - prima.y);
});

function finePuntatore(e) {
  if (!puntatori.delete(e.pointerId)) return;
  if (e.type === 'pointerup' && !trascinato && puntatori.size === 0) clic(e);
  if (puntatori.size < 2) distanzaDita = 0;
}
tela.addEventListener('pointerup', finePuntatore);
tela.addEventListener('pointercancel', finePuntatore);
tela.addEventListener('pointerleave', (e) => {
  $('suggerimento').hidden = true;
  if (e.pointerType === 'mouse' && !puntatori.size) estrai(null);  // al tocco pointerleave arriva dopo ogni tocco
});

tela.addEventListener('wheel', (e) => {
  e.preventDefault();
  const passi = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY;  // Firefox a volte conta in righe
  zooma(Math.exp(-passi * 0.0015));
}, { passive: false });

// tastiera sulla scena: frecce su e giu' scorrono il piano che esce, Invio entra (l'elenco 2D resta il percorso completo)
tela.tabIndex = 0;
tela.setAttribute('aria-label', 'Edificio: frecce su e giù per scegliere un piano, Invio per entrare');
tela.addEventListener('keydown', (e) => {
  if (pianoAperto || !edificio?.piani.length) return;
  if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
    e.preventDefault();
    usaPuntatore('tastiera');
    const ordine = [...edificio.piani].sort((a, b) => a.numero - b.numero);
    const i = ordine.findIndex((p) => p.id === estratto), su = e.key === 'ArrowUp';
    const j = i < 0 ? (su ? 0 : ordine.length - 1) : THREE.MathUtils.clamp(i + (su ? 1 : -1), 0, ordine.length - 1);
    estrai(ordine[j].id);
  } else if (e.key === 'Enter' && estratto) {
    e.preventDefault();
    apriPiano(estratto);
  }
});

// ===== Rete =====
async function leggiJSON(url) {
  const r = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(5000) });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

// POST JSON; se il server dice di no, l'errore porta il suo testo
async function inviaJSON(url, dati) {
  let r;
  try {
    r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(dati), signal: AbortSignal.timeout(10000) });
  } catch {
    throw new Error('Ufficio non raggiungibile');
  }
  if (r.ok) return r.json().catch(() => ({}));
  let testo = `Errore ${r.status}`;
  try { testo = (await r.json()).errore || testo; } catch { /* corpo non JSON */ }
  throw new Error(testo);
}

function raggiungibile(si) { $('avviso').hidden = si; }

let ultimiAttivi = '', statoInCorso = false;
async function aggiornaStato() {
  if (statoInCorso) return;
  statoInCorso = true;
  try {
    stato = await leggiJSON('/api/stato');
    raggiungibile(true);
    aggiornaHud();  // la RAM del Pi cambia anche senza cambi di stato
    const attivi = JSON.stringify([stato.lavorando, stato.in_coda, stato.flussi || [], stato.eventi || {}, !!stato.attesa_ram]);
    if (attivi !== ultimiAttivi) {  // si ridisegna solo se qualcuno ha cambiato stato
      ultimiAttivi = attivi;
      applicaStato();
    }
  } catch {
    raggiungibile(false);
  } finally {
    statoInCorso = false;
  }
}

// ===== Avvio =====
async function avvia() {
  try {
    edificio = await leggiJSON('/api/edificio');
  } catch {
    raggiungibile(false);
    setTimeout(avvia, INTERVALLO_STATO);
    return;
  }
  raggiungibile(true);
  for (const p of edificio.piani) {
    p.larghezza *= ALLARGA.x;
    p.profondita *= ALLARGA.z;
    for (const a of p.agenti) {
      a.piano = p.id;
      // pianta allargata e robot piu' grandi (vedi ALLARGA e ROBOT)
      a.x *= a.x < 0 ? ALLARGA.capi : ALLARGA.x;
      a.z *= ALLARGA.z;
      a.scala *= ROBOT;
      agentiPerId.set(a.id, a);
    }
  }
  if ($('btn-greta')) $('btn-greta').hidden = !agentiPerId.has('segretaria');
  costruisciEsterno();
  tornaFuori();
  applicaStato();
  aggiornaStato();
  setInterval(aggiornaStato, INTERVALLO_STATO);
}
avvia();
