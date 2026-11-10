//! Differential harness for native publication policy and portable package formats.
use thor_compiler::{
    diagnostic,
    publishing::{deps, icons, package as publisher, policy},
};
fn main() {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args[0] == "publisher" {
        let packet: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&args[1]).unwrap()).unwrap();
        let string = |key: &str| packet["options"][key].as_str().map(str::to_string);
        let paths = |key: &str| {
            packet["options"][key]
                .as_array()
                .into_iter()
                .flatten()
                .map(|value| value.as_str().unwrap().to_string())
                .collect::<Vec<_>>()
        };
        let options = publisher::Options {
            name: string("app_name"),
            identifier: string("app_id"),
            version: string("app_version"),
            icon: string("app_icon"),
            app_type: string("app_type"),
            resources: paths("app_resource"),
            libraries: paths("app_library"),
            directory: string("publish_dir").map(std::path::PathBuf::from),
        };
        let flags = packet["flags"]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| value.as_str().unwrap().to_string())
            .collect::<Vec<_>>();
        let result=publisher::Publisher::prepare(std::path::Path::new(packet["entry"].as_str().unwrap()),&flags,&options,packet["system"].as_str().unwrap(),packet["arch"].as_str().unwrap()).and_then(|mut p|{
 let native=if packet["native"].as_bool()==Some(true){Some(p.native_flags(&flags,std::path::Path::new(packet["scratch"].as_str().unwrap()))?)}else{None};
 if let Some(executable)=packet["executable"].as_str(){p.package(std::path::Path::new(executable),std::path::Path::new(packet["scratch"].as_str().unwrap()))?;}
 Ok(serde_json::json!({"root":p.root,"system":p.system,"arch":p.arch,"name":p.name,"executable_name":p.executable_name,"identifier":p.identifier,"version":p.version,"app_type":p.app_type,"output":p.output,"icon":p.icon,"image":p.image.as_ref().map(|image|[image.width(),image.height()]),"resources":p.resources.keys().collect::<Vec<_>>(),"directories":p.directories,"libraries":p.libraries,"warnings":p.warnings,"native_flags":native,"linked_file":p.linked_file}))
});
        match result {
            Ok(value) => println!("{}", serde_json::json!({"ok":value})),
            Err(error) => println!("{}", serde_json::json!({"error":error.to_string()})),
        };
        return;
    }
    if args[0] == "policy" {
        let packet: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&args[1]).unwrap()).unwrap();
        let value = packet["value"].as_str().unwrap_or_default();
        let root = std::path::Path::new(packet["root"].as_str().unwrap_or("."));
        let result:diagnostic::Result<serde_json::Value>=match packet["action"].as_str().unwrap(){
"relative"=>policy::relative_path(value,"Resource").map(|path|serde_json::json!(path)),
"name"=>policy::validate_name(value,"Application name").map(|name|serde_json::json!(name)),
"quote"=>Ok(serde_json::json!(policy::shell_quote(value))),
"config"=>policy::read_config(root).map(|settings|serde_json::json!({"text":settings.text,"resources":settings.resources,"libraries":settings.libraries})),
"resources"=>policy::collect_resources(root,&packet["entries"].as_array().unwrap().iter().map(|value|value.as_str().unwrap().to_string()).collect::<Vec<_>>())
.map(|resources|serde_json::json!({"files":resources.files.keys().collect::<Vec<_>>(),"directories":resources.directories})),
_=>Err(diagnostic::Error::new("unknown policy action"))};
        match result {
            Ok(value) => println!("{}", serde_json::json!({"ok":value})),
            Err(error) => println!("{}", serde_json::json!({"error":error.to_string()})),
        };
        return;
    }
    if args[0] == "sign" {
        match deps::sign_macos(std::path::Path::new(&args[1])) {
            Ok(()) => println!("{}", serde_json::json!({"ok":true})),
            Err(error) => println!("{}", serde_json::json!({"error":error.to_string()})),
        };
        return;
    }
    if args[0] == "pe" {
        match deps::pe_imports(std::path::Path::new(&args[1])) {
            Ok(value) => println!("{}", serde_json::json!({"ok":value})),
            Err(error) => println!("{}", serde_json::json!({"error":error.to_string()})),
        };
        return;
    }
    if args[0] == "bundle" {
        let packet: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&args[1]).unwrap()).unwrap();
        let path = |name: &str| std::path::Path::new(packet[name].as_str().unwrap());
        let paths = |name: &str| {
            packet[name]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| std::path::PathBuf::from(v.as_str().unwrap()))
                .collect::<Vec<_>>()
        };
        match deps::bundle(
            packet["system"].as_str().unwrap(),
            path("executable"),
            path("binary"),
            path("destination"),
            path("project"),
            &paths("declared"),
            &paths("hints"),
        ) {
            Ok(value) => println!("{}", serde_json::json!({"ok":value})),
            Err(error) => println!("{}", serde_json::json!({"error":error.to_string()})),
        };
        return;
    }
    let mut warnings = vec![];
    match icons::normalize(std::path::Path::new(&args[0]), &mut warnings).and_then(|image| {
        let dimensions = (image.width(), image.height());
        icons::write(
            &image,
            std::path::Path::new(&args[0]),
            std::path::Path::new(&args[1]),
            &args[2],
        )?;
        Ok(dimensions)
    }) {
        Ok((width, height)) => {
            println!("{width} {height}");
            for warning in warnings {
                println!("{warning}");
            }
        }
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1)
        }
    }
}
