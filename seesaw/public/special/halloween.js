// Pumpkins by https://openclipart.org/artist/Simanek
const images = [
    "/special/halloween/brian.png",
    "/special/halloween/chuck.png",
    "/special/halloween/frank.png",
    "/special/halloween/fred.png",
    "/special/halloween/garth.png",
    "/special/halloween/ignac.png",
    "/special/halloween/igor.png",
    "/special/halloween/jack.png",
    "/special/halloween/jimmy.png",
    "/special/halloween/leonard.png",
    "/special/halloween/lou.png",
    "/special/halloween/pete.png",
    "/special/halloween/randy.png",
    "/special/halloween/shmalvin.png",
    "/special/halloween/trent.png",
    "/special/halloween/tyrell.png",
    "/special/halloween/vladimir.png",
    "/special/halloween/wolfy.png",
];

const pick = images[Math.floor(Math.random() * images.length)];
const h1 = document.querySelector("#warrior-logo h1");
const img = new Image();
img.onload = () => h1.style.setProperty("--bg", `url("${pick}")`);
img.src = pick;
