using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: System.Reflection.AssemblyTitle("Office Setup")]
[assembly: System.Reflection.AssemblyDescription("File associations for locally modified portable Craft applications")]
[assembly: System.Reflection.AssemblyVersion("1.0.0.0")]

internal sealed class OfficeApp
{
    internal string Id, Name, Original, Folder, Exe;
    internal string[] Extensions;
    internal OfficeApp(string id, string name, string original, string folder, string exe, string[] extensions)
    { Id=id; Name=name; Original=original; Folder=folder; Exe=exe; Extensions=extensions; }
    internal string Locate(string root)
    {
        string[] candidates = { Path.Combine(root,Folder,Name+".exe"), Path.Combine(root,Name,Name+".exe"), Path.Combine(root,Folder,Exe) };
        foreach(string path in candidates) if(File.Exists(path)) return Path.GetFullPath(path);
        return null;
    }
}

internal sealed class AssociationStore
{
    internal const string Owner="CraftPortable-Associations-v1-82fda779";
    private readonly string prefix;
    internal AssociationStore(string testPrefix) { prefix=testPrefix; }
    private string Key(string relative) { return prefix+relative; }
    private string AppPath(OfficeApp app) { return "Software\\CraftPortable\\Associations\\"+app.Id; }
    internal string RegisteredName(OfficeApp app) { return "CraftPortable."+app.Id; }
    private string ProgId(OfficeApp app) { return "CraftPortable."+app.Id+".Document"; }
    private string ProgKey(OfficeApp app) { return "Software\\Classes\\"+ProgId(app); }
    private string MenuKey(OfficeApp app,string ext) { return "Software\\Classes\\SystemFileAssociations\\"+ext+"\\shell\\CraftPortable."+app.Id; }
    private string NewKey(OfficeApp app) { return "Software\\Classes\\"+app.Extensions[0]+"\\ShellNew"; }
    private string NewLabel(OfficeApp app)
    { return app.Id=="Excel"?"Таблица Excel":app.Id=="PowerPoint"?"Презентация PowerPoint":app.Id=="PDF"?"Документ PDF":"Документ Word"; }
    private byte[] Template(OfficeApp app)
    {
        using(var stream=System.Reflection.Assembly.GetExecutingAssembly().GetManifestResourceStream("CraftPortable."+app.Id+".Template"))
        { if(stream==null)throw new FileNotFoundException("В утилите отсутствует шаблон "+app.Name);using(var memory=new MemoryStream()){stream.CopyTo(memory);return memory.ToArray();} }
    }
    private bool Exists(string key) { using(var k=Registry.CurrentUser.OpenSubKey(Key(key))) return k!=null; }
    private bool Owned(string key)
    { using(var k=Registry.CurrentUser.OpenSubKey(Key(key))) return k!=null && (string)k.GetValue("CraftPortableOwner",null)==Owner; }
    private void Guard(string key)
    { if(Exists(key) && !Owned(key)) throw new InvalidOperationException("Обнаружена чужая запись: "+key+". Она не изменена."); }
    internal bool CanAddNew(OfficeApp app)
    {
        if(Owned(NewKey(app)))return true;
        if(Exists(NewKey(app)))return false;
        if(prefix.Length==0)using(var merged=Registry.ClassesRoot.OpenSubKey(app.Extensions[0]+"\\ShellNew"))if(merged!=null)return false;
        return true;
    }
    private void Put(string key,string name,string value)
    { using(var k=Registry.CurrentUser.CreateSubKey(Key(key))) k.SetValue(name,value,RegistryValueKind.String); }
    private void Mark(string key) { Put(key,"CraftPortableOwner",Owner); }
    private void DropOwned(string key)
    { if(Owned(key)) Registry.CurrentUser.DeleteSubKeyTree(Key(key),false); }
    internal bool Registered(OfficeApp app) { return Owned(AppPath(app)) && Owned(ProgKey(app)); }
    internal bool HasMenu(OfficeApp app) { return Owned(NewKey(app)) || Owned(MenuKey(app,app.Extensions[0])); }
    internal void Register(OfficeApp app,string exe,bool menu)
    {
        if(exe==null || !Path.IsPathRooted(exe) || !File.Exists(exe)) throw new FileNotFoundException("Не найдена программа "+app.Name);
        Guard(AppPath(app)); Guard(ProgKey(app));
        foreach(string ext in app.Extensions) Guard(MenuKey(app,ext));
        byte[] template=null;
        if(menu) {
            if(!CanAddNew(app))throw new InvalidOperationException("Пункт «Создать» для "+app.Extensions[0]+" уже добавлен другой программой. Он сохранён. Снимите галочку подменю «Создать», чтобы добавить только ассоциации.");
            template=Template(app);
        }
        using(var reg=Registry.CurrentUser.OpenSubKey(Key("Software\\RegisteredApplications")))
        {
            object previous=reg==null?null:reg.GetValue(RegisteredName(app));
            if(previous!=null && !String.Equals(previous as string,AppPath(app)+"\\Capabilities",StringComparison.Ordinal))
                throw new InvalidOperationException("Имя регистрации уже используется другой программой.");
        }
        string command="\""+exe+"\" \"%1\"";
        Mark(AppPath(app)); Mark(ProgKey(app));
        Put(AppPath(app),"Executable",exe);
        Put(ProgKey(app),"",NewLabel(app));
        Put(ProgKey(app),"FriendlyTypeName",NewLabel(app));
        Put(ProgKey(app)+"\\DefaultIcon","", "\""+exe+"\",0");
        Put(ProgKey(app)+"\\shell\\open\\command","",command);
        Put(ProgKey(app)+"\\Application","ApplicationName",app.Name);
        Put(ProgKey(app)+"\\Application","ApplicationDescription",app.Original+" — локальная portable-сборка");
        string cap=AppPath(app)+"\\Capabilities";
        Put(cap,"ApplicationName",app.Name);
        Put(cap,"ApplicationDescription",app.Original+" — модифицированная portable-сборка. Не Microsoft Office.");
        Put(cap,"ApplicationIcon","\""+exe+"\",0");
        foreach(string ext in app.Extensions)
        {
            Put(cap+"\\FileAssociations",ext,ProgId(app));
            using(var k=Registry.CurrentUser.CreateSubKey(Key("Software\\Classes\\"+ext+"\\OpenWithProgids")))
                k.SetValue(ProgId(app),new byte[0],RegistryValueKind.None);
            // Remove obsolete verbs from the first helper version; associations provide Open With.
            DropOwned(MenuKey(app,ext));
        }
        if(menu)
        {
            string extension="Software\\Classes\\"+app.Extensions[0];
            string current=null;
            using(var k=Registry.CurrentUser.OpenSubKey(Key(extension))) current=k==null?null:k.GetValue("") as string;
            // Only supply a type for an unregistered extension. Never replace another type's mapping.
            if(String.IsNullOrEmpty(current))
            {
                if(prefix.Length==0) using(var merged=Registry.ClassesRoot.OpenSubKey(app.Extensions[0])) current=merged==null?null:merged.GetValue("") as string;
                if(String.IsNullOrEmpty(current)) {Put(extension,"",ProgId(app));Put(extension,"CraftPortableDefaultOwner",Owner);}
            }
            string nk=NewKey(app);Mark(nk);Put(nk,"ItemName",NewLabel(app));Put(nk,"IconPath","\""+exe+"\",0");
            using(var k=Registry.CurrentUser.CreateSubKey(Key(nk)))k.SetValue("Data",template,RegistryValueKind.Binary);
        }
        else RemoveNew(app);
        Put("Software\\RegisteredApplications",RegisteredName(app),cap);
    }
    private void RemoveNew(OfficeApp app)
    {
        DropOwned(NewKey(app));
        using(var k=Registry.CurrentUser.OpenSubKey(Key("Software\\Classes\\"+app.Extensions[0]),true))
        {
            if(k!=null && (string)k.GetValue("CraftPortableDefaultOwner",null)==Owner)
            { if((string)k.GetValue("",null)==ProgId(app))k.DeleteValue("",false);k.DeleteValue("CraftPortableDefaultOwner",false); }
        }
    }
    internal void Remove(OfficeApp app)
    {
        // Protected Explorer\FileExts\UserChoice is never written.
        if(!Owned(AppPath(app))) return;
        RemoveNew(app);
        foreach(string ext in app.Extensions)
        {
            string path="Software\\Classes\\"+ext+"\\OpenWithProgids";
            using(var k=Registry.CurrentUser.OpenSubKey(Key(path),true))
            {
                if(k!=null && k.GetValue(ProgId(app),null)!=null && k.GetValueKind(ProgId(app))==RegistryValueKind.None)
                    k.DeleteValue(ProgId(app),false);
            }
            DropOwned(MenuKey(app,ext));
        }
        using(var k=Registry.CurrentUser.OpenSubKey(Key("Software\\RegisteredApplications"),true))
            if(k!=null && String.Equals(k.GetValue(RegisteredName(app)) as string,AppPath(app)+"\\Capabilities",StringComparison.Ordinal))
                k.DeleteValue(RegisteredName(app),false);
        DropOwned(ProgKey(app)); DropOwned(AppPath(app));
    }
}

internal sealed class SetupWindow : Form
{
    internal static readonly OfficeApp[] Apps={
        new OfficeApp("Word","Word","WordCraft","WordCraft","wordcraft.exe",new[]{".docx",".doc",".odt",".rtf",".txt"}),
        new OfficeApp("Excel","Excel","GridCraft","GridCraft","gridcraft.exe",new[]{".xlsx",".xls",".csv"}),
        new OfficeApp("PowerPoint","PowerPoint","DeckCraft","DeckCraft","deckcraft.exe",new[]{".pptx"}),
        new OfficeApp("PDF","PDF","PdfCraft","PdfCraft","pdfcraft.exe",new[]{".pdf"})
    };
    private readonly string root;
    private readonly AssociationStore store;
    private readonly Label status=new Label();
    private readonly CheckBox context=new CheckBox();
    private readonly Label[] locations=new Label[Apps.Length];
    [DllImport("shell32.dll")] private static extern void SHChangeNotify(uint eventId,uint flags,IntPtr item1,IntPtr item2);
    internal SetupWindow(string directory,AssociationStore registry)
    {
        root=directory;store=registry;
        Text="Office Setup"; Font=new Font("Segoe UI",10); ClientSize=new Size(790,520);
        AutoScaleMode=AutoScaleMode.Dpi; MinimumSize=new Size(760,540); StartPosition=FormStartPosition.CenterScreen;
        var panel=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=7,Padding=new Padding(22)};
        panel.RowStyles.Add(new RowStyle(SizeType.Absolute,45));panel.RowStyles.Add(new RowStyle(SizeType.Absolute,65));
        panel.RowStyles.Add(new RowStyle(SizeType.Percent,100));panel.RowStyles.Add(new RowStyle(SizeType.Absolute,32));
        panel.RowStyles.Add(new RowStyle(SizeType.Absolute,44));panel.RowStyles.Add(new RowStyle(SizeType.Absolute,52));panel.RowStyles.Add(new RowStyle(SizeType.Absolute,35));
        Controls.Add(panel);
        panel.Controls.Add(new Label{Text="Открывайте файлы привычным способом",Font=new Font(Font.FontFamily,17,FontStyle.Bold),Dock=DockStyle.Fill});
        panel.Controls.Add(new Label{Text="Добавьте программы в Windows, затем выберите нужную программу по умолчанию.\nНикакой установки: настройки действуют для вашей учётной записи.",Dock=DockStyle.Fill});
        var grid=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=3,RowCount=4};
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,135));grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,215));
        for(int i=0;i<Apps.Length;i++)
        {
            OfficeApp app=Apps[i];grid.RowStyles.Add(new RowStyle(SizeType.Percent,25));
            grid.Controls.Add(new Label{Text=app.Name,Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleLeft,Font=new Font(Font,FontStyle.Bold)},0,i);
            locations[i]=new Label{Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleLeft,AutoEllipsis=true}; grid.Controls.Add(locations[i],1,i);
            var button=new Button{Text="Выбрать по умолчанию",Dock=DockStyle.Fill,Margin=new Padding(5)};
            button.Click+=delegate { Run(delegate { Register(app);Process.Start(new ProcessStartInfo("ms-settings:defaultapps?registeredAppUser="+Uri.EscapeDataString(store.RegisteredName(app))){UseShellExecute=true});status.Text="В открывшемся окне Windows выберите программу для нужных форматов."; }); };
            grid.Controls.Add(button,2,i);
        }
        panel.Controls.Add(grid);
        context.Text="Добавить документы в подменю «Создать»";context.Checked=true;context.Dock=DockStyle.Fill;panel.Controls.Add(context);
        var actions=new FlowLayoutPanel{Dock=DockStyle.Fill,WrapContents=false};
        var add=new Button{Text="Добавить все программы",AutoSize=true,Height=34};
        add.Click+=delegate { Run(delegate { int count=0;foreach(var app in Apps) if(app.Locate(root)!=null) {Register(app);count++;}if(count==0) throw new FileNotFoundException("Положите Office Setup.exe рядом с папками программ.");status.Text="Добавлено программ: "+count+". Теперь можно выбрать их по умолчанию."; }); };
        var remove=new Button{Text="Убрать интеграцию",AutoSize=true,Height=34};
        remove.Click+=delegate { if(MessageBox.Show(this,"Если эти программы выбраны по умолчанию, сначала выберите им замену в Windows.\n\nУбрать созданные утилитой записи? Программы и документы останутся на месте.","Убрать интеграцию",MessageBoxButtons.YesNo,MessageBoxIcon.Question)==DialogResult.Yes) Run(delegate {foreach(var app in Apps)store.Remove(app);SHChangeNotify(0x08000000,0,IntPtr.Zero,IntPtr.Zero);status.Text="Собственные записи интеграции удалены. Файлы не изменены.";}); };
        actions.Controls.Add(add);actions.Controls.Add(remove);panel.Controls.Add(actions);
        status.Dock=DockStyle.Fill;status.ForeColor=Color.FromArgb(30,75,115);panel.Controls.Add(status);
        panel.Controls.Add(new Label{Text="Portable-сборки Craft; не Microsoft Office. После переноса папки повторите добавление.",Dock=DockStyle.Fill,ForeColor=Color.DimGray});
        RefreshState();
    }
    private void Register(OfficeApp app) { store.Register(app,app.Locate(root),context.Checked);SHChangeNotify(0x08000000,0,IntPtr.Zero,IntPtr.Zero); }
    private void RefreshState()
    { for(int i=0;i<Apps.Length;i++) locations[i].Text=Apps[i].Locate(root)==null?"Программа не найдена":String.Join(", ",Apps[i].Extensions)+(store.Registered(Apps[i])?" · добавлено":""); }
    private void Run(Action action)
    { try {action();} catch(Exception e) {status.Text="Не удалось выполнить действие.";MessageBox.Show(this,e.Message,"Office Setup",MessageBoxButtons.OK,MessageBoxIcon.Warning);}finally{RefreshState();} }
}

internal static class Program
{
    [DllImport("shell32.dll")] private static extern void SHChangeNotify(uint eventId,uint flags,IntPtr item1,IntPtr item2);
    [STAThread] private static void Main()
    { Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);Application.Run(new SetupWindow(AppDomain.CurrentDomain.BaseDirectory,new AssociationStore(""))); }
    public static string RefreshExisting(string root)
    {
        var store=new AssociationStore(""); int count=0;
        foreach(var app in SetupWindow.Apps) if(store.Registered(app))
        { store.Register(app,app.Locate(root),store.HasMenu(app) && store.CanAddNew(app));count++; }
        SHChangeNotify(0x08000000,0,IntPtr.Zero,IntPtr.Zero);
        return "Updated existing registrations: "+count;
    }
    // All test writes are confined beneath one unique HKCU sandbox, never real file associations.
    public static string SelfTest()
    {
        string test="Software\\CraftPortable.Setup.Tests\\"+Guid.NewGuid().ToString("N")+"\\";
        var store=new AssociationStore(test); string exe=System.Reflection.Assembly.GetExecutingAssembly().Location;
        int checks=0;
        try
        {
            foreach(var app in SetupWindow.Apps)
            {
                store.Register(app,exe,true); if(!store.Registered(app))throw new Exception("register");checks++;
                using(var k=Registry.CurrentUser.OpenSubKey(test+"Software\\Classes\\CraftPortable."+app.Id+".Document\\Application"))
                    if((string)k.GetValue("ApplicationName")!=app.Name)throw new Exception("incorrect displayed name");checks++;
                using(var k=Registry.CurrentUser.OpenSubKey(test+"Software\\Classes\\"+app.Extensions[0]+"\\ShellNew"))
                { var data=k==null?null:k.GetValue("Data") as byte[];if(data==null || data.Length<100)throw new Exception("missing valid New template"); }checks++;
                using(var k=Registry.CurrentUser.OpenSubKey(test+"Software\\Classes\\SystemFileAssociations\\"+app.Extensions[0]+"\\shell\\CraftPortable."+app.Id))
                    if(k!=null)throw new Exception("obsolete Open verb exists");checks++;
                store.Register(app,exe,false);checks++;
                using(var k=Registry.CurrentUser.OpenSubKey(test+"Software\\Classes\\"+app.Extensions[0]+"\\ShellNew"))
                    if(k!=null)throw new Exception("New menu removal failed");checks++;
                string extKey=test+"Software\\Classes\\"+app.Extensions[0]+"\\OpenWithProgids";
                using(var k=Registry.CurrentUser.CreateSubKey(extKey)) k.SetValue("Other.Application",new byte[0],RegistryValueKind.None);
                store.Remove(app);if(store.Registered(app))throw new Exception("remove");checks++;
                using(var k=Registry.CurrentUser.OpenSubKey(extKey)) if(k.GetValue("Other.Application",null)==null)throw new Exception("foreign value lost");checks++;
                string foreign=test+"Software\\Classes\\CraftPortable."+app.Id+".Document";
                using(var k=Registry.CurrentUser.CreateSubKey(foreign)) k.SetValue("Foreign","keep");
                bool refused=false;try{store.Register(app,exe,true);}catch(InvalidOperationException){refused=true;}
                if(!refused)throw new Exception("foreign key overwritten");checks++;
            }
            return "PASS: "+checks+" checks; isolated HKCU sandbox; no actual associations changed.";
        }
        finally {Registry.CurrentUser.DeleteSubKeyTree(test.TrimEnd('\\'),false);}
    }
}
