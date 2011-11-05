"""Base views.
"""
from django.http import HttpResponse
from django.template import RequestContext
from django import forms
from django.utils import simplejson
from django.shortcuts import render, redirect

from boto.exception import EC2ResponseError

from biocloudcentral.amazon.launch import (connect_ec2, create_iam_user,
                                           create_cm_security_group,
                                           create_key_pair, run_instance,
                                           instance_state)

# Keep user data file template here so no indentation in the file is introduced at print time
UD = """cluster_name : {cluster_name}
password: {password}
freenxpass: {password}
access_key: {access_key}
secret_key: {secret_key}
"""
# ## Landing page with redirects

def home(request):
    launch_url = request.build_absolute_uri("/launch")
    if launch_url.startswith(("http://127.0.0.1", "http://localhost")):
        return redirect("/launch")
    else:
        return redirect("https://biocloudcentral.herokuapp.com/launch")

# ## CloudMan launch and configuration entry details

class CloudManForm(forms.Form):
    """Details needed to boot a setup and boot a CloudMan instance.
    """
    key_url = "https://aws-portal.amazon.com/gp/aws/developer/account/index.html?action=access-key"
    target = "target='_blank'"
    iam_url = "http://aws.amazon.com/iam/"
    cluster_name = forms.CharField(required=True,
                                   help_text="Name of your cluster used for identification. "
                                   "This can be any name you choose.")
    password = forms.CharField(widget=forms.PasswordInput(render_value=False),
                               help_text="Your choice of password, for the CloudMan " \
                               "web interface and accessing the Amazon instance via ssh or FreeNX.")
    access_key = forms.CharField(required=True,
                                 help_text="Your Amazon Access Key ID. Available from "
                                 "the <a href='{0}' {1}>security credentials page</a>.".format(
                                     key_url, target))
    secret_key = forms.CharField(required=True,
                                 help_text="Your Amazon Secret Access Key. Also available "
                                 "from the <a href='{0}' {1}>security credentials page</a>.".format(
                                     key_url, target))
    use_iam = forms.BooleanField(required=False, label="Use IAM", initial=True,
                                 help_text="If checked, use <a href='{0}' {1}>AWS IAM</a> and "
                                 "create a new set of access keys. Else, start the cluster "
                                 "with the provided credentials.".format(iam_url, target))
    instance_type = forms.ChoiceField((("m1.large", "Large"),
                                       ("t1.micro", "Micro"),
                                       ("m1.xlarge", "Extra Large")),
                            help_text="Amazon <a href='{0}' {1}>instance type</a> to start.".format(
                                      "http://aws.amazon.com/ec2/#instance", target))

def launch(request):
    """Configure and launch CloudBioLinux and CloudMan servers.
    """
    if request.method == "POST":
        form = CloudManForm(request.POST)
        if form.is_valid():
            print form.cleaned_data
            ec2_error = None
            try:
                # Create security group & key pair with original creds and then,
                # optionally, create IAM identity that will run the cluster but
                # have reduced set of privileges
                ec2_conn = connect_ec2(form.cleaned_data['access_key'],
                                       form.cleaned_data['secret_key'])
                sg_name = create_cm_security_group(ec2_conn)
                kp_name = create_key_pair(ec2_conn)
                if form.cleaned_data['use_iam'] is True:
                    a_key, s_key = create_iam_user(form.cleaned_data['access_key'],
                                                   form.cleaned_data['secret_key'])
                else:
                    a_key = form.cleaned_data['access_key']
                    s_key = form.cleaned_data['secret_key']
                if a_key is None or s_key is None:
                    ec2_error = "Could not generate IAM access keys. Not starting an instance."
            except EC2ResponseError, ec2_error:
                pass
            # associate form data with session for starting instance
            # and supplying download files
            if ec2_error is None:
                form.cleaned_data["access_key"] = a_key
                form.cleaned_data["secret_key"] = s_key
                form.cleaned_data["kp_name"] = kp_name
                form.cleaned_data["sg_name"] = sg_name
                request.session["ec2data"] = form.cleaned_data
                if runinstance(request):
                    return redirect("/monitor")
                else:
                    form.non_field_errors = "A problem starting EC2 instance. " \
                                            "Check AWS console."
            else:
                form.non_field_errors = ec2_error.error_message
    else:
        form = CloudManForm()
    return render(request, "launch.html", {"form": form})

def monitor(request):
    """Monitor a launch request and return offline files for console re-runs.
    """
    # ec2data = request.session.get("ec2data", {})
    return render(request, "monitor.html", context_instance=RequestContext(request))

def runinstance(request):
    """Run a CloudBioLinux/CloudMan instance with current session credentials.
    """
    form = request.session["ec2data"]
    rs = None
    # Recreate EC2 connection with newly created creds
    ec2_conn = connect_ec2(form["access_key"], form["secret_key"])
    rs = run_instance(ec2_conn=ec2_conn,
                      user_provided_data=form,
                      key_name=form["kp_name"],
                      security_groups=[form["sg_name"]])
    if rs is not None:
        request.session['ec2data']['instance_id'] = rs.instances[0].id
        request.session['ec2data']['public_dns'] = rs.instances[0].public_dns_name
        return True
    else:
        return False

def userdata(request):
    """Provide file download of user-data to re-start an instance.
    """
    ec2data = request.session["ec2data"]
    response = HttpResponse(mimetype='text/plain')
    response['Content-Disposition'] = 'attachment; filename={cluster_name}-userdata.txt'.format(
        **ec2data)
    response.write(UD.format(**ec2data))
    return response

def instancestate(request):
    form = request.session["ec2data"]
    ec2_conn = connect_ec2(form["access_key"], form["secret_key"])
    state = {'instance_state': instance_state(ec2_conn, form['instance_id'])}
    return HttpResponse(simplejson.dumps(state), mimetype="application/json")